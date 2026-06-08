import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, List, Optional

from loguru import logger

from embykeeper.config import config
from embykeeper.schema import Config, EmbyAccount, EmbyConfig

from .crypto import decrypt_token

logger = logger.bind(scheme="embykeeperapi")


# Web-managed account data store
WEB_ACCOUNTS_FILE = "web_accounts.json"
WEB_ACCOUNT_BOOL_FIELDS = {"allow_multiple", "allow_stream", "use_proxy", "enabled"}
WEB_ACCOUNT_TEXT_FIELDS = {
    "name",
    "play_id",
    "useragent",
    "client",
    "client_version",
    "device",
    "device_id",
    "time_range",
}
WEB_ACCOUNT_AUTH_METHODS = {"token", "password"}
_DROP_FIELD = object()


def _sanitize_optional_bool(value):
    if value is None:
        return _DROP_FIELD
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return _DROP_FIELD


def _sanitize_positive_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            number = int(value, 10)
        except ValueError:
            return None
    else:
        return None
    return number if number > 0 else None


def _sanitize_watch_time(value):
    if value is None:
        return _DROP_FIELD

    single_value = _sanitize_positive_int(value)
    if single_value is not None:
        return single_value

    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return _DROP_FIELD
    min_time = _sanitize_positive_int(value[0])
    max_time = _sanitize_positive_int(value[1])
    if min_time is None or max_time is None or min_time > max_time:
        return _DROP_FIELD
    return [min_time, max_time]


def _sanitize_optional_text(value):
    if value is None:
        return _DROP_FIELD
    if not isinstance(value, str):
        return _DROP_FIELD
    value = value.strip()
    return value or _DROP_FIELD


def _sanitize_auth_method(value):
    if not isinstance(value, str):
        return _DROP_FIELD
    value = value.strip().lower()
    if value not in WEB_ACCOUNT_AUTH_METHODS:
        return _DROP_FIELD
    return value


def _sanitize_interval_days(value):
    if value is None or isinstance(value, bool):
        return _DROP_FIELD
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return _DROP_FIELD
    value = value.strip()
    return value or _DROP_FIELD


def _sanitize_account_record(data) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    sanitized = deepcopy(data)
    for field in ("url", "username"):
        value = sanitized.get(field)
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        sanitized[field] = value
    for field in WEB_ACCOUNT_BOOL_FIELDS:
        if field not in sanitized:
            continue
        value = _sanitize_optional_bool(sanitized[field])
        if value is _DROP_FIELD:
            sanitized.pop(field, None)
        else:
            sanitized[field] = value
    for field in WEB_ACCOUNT_TEXT_FIELDS:
        if field not in sanitized:
            continue
        value = _sanitize_optional_text(sanitized[field])
        if value is _DROP_FIELD:
            sanitized.pop(field, None)
        else:
            sanitized[field] = value
    if "time" in sanitized:
        value = _sanitize_watch_time(sanitized["time"])
        if value is _DROP_FIELD:
            sanitized.pop("time", None)
        else:
            sanitized["time"] = value
    if "interval_days" in sanitized:
        value = _sanitize_interval_days(sanitized["interval_days"])
        if value is _DROP_FIELD:
            sanitized.pop("interval_days", None)
        else:
            sanitized["interval_days"] = value
    if "auth_method" in sanitized:
        value = _sanitize_auth_method(sanitized["auth_method"])
        if value is _DROP_FIELD:
            sanitized.pop("auth_method", None)
        else:
            sanitized["auth_method"] = value
    return sanitized


def _backup_invalid_accounts_file(filepath: Path) -> bool:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = filepath.with_name(f"{filepath.name}.corrupt.{timestamp}")
    counter = 1
    while backup_path.exists():
        backup_path = filepath.with_name(f"{filepath.name}.corrupt.{timestamp}.{counter}")
        counter += 1
    try:
        filepath.replace(backup_path)
        logger.warning(f"Backed up invalid web accounts file to {backup_path.name}.")
        return True
    except OSError as e:
        logger.warning(f"Failed to back up invalid web accounts file: {e}")
        return False


class WebAccountData:
    """Manages Emby accounts created via the web UI."""

    def __init__(self, basedir: Path):
        self.basedir = basedir
        self.basedir.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self):
        filepath = self.basedir / WEB_ACCOUNTS_FILE
        if filepath.is_file():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("web accounts data must be an object")
                valid_data = {}
                for key, value in data.items():
                    sanitized = _sanitize_account_record(value)
                    if sanitized is not None:
                        valid_data[key] = sanitized
                if len(valid_data) != len(data):
                    logger.warning("Web accounts file contains invalid account records; backing up original.")
                    if _backup_invalid_accounts_file(filepath):
                        try:
                            self._save(valid_data)
                        except OSError as e:
                            logger.warning(f"Failed to write sanitized web accounts file: {e}")
                elif valid_data != data:
                    logger.warning(
                        "Web accounts file contains unnormalized account records; backing up original."
                    )
                    if _backup_invalid_accounts_file(filepath):
                        try:
                            self._save(valid_data)
                        except OSError as e:
                            logger.warning(f"Failed to write sanitized web accounts file: {e}")
                self._data = valid_data
            except json.JSONDecodeError:
                logger.warning("Web accounts file corrupted, starting fresh.")
                _backup_invalid_accounts_file(filepath)
                self._data = {}
            except OSError:
                logger.warning("Failed to read web accounts file, starting fresh.")
                self._data = {}
            except ValueError as e:
                logger.warning(f"Web accounts file invalid: {e}; starting fresh.")
                _backup_invalid_accounts_file(filepath)
                self._data = {}

    def _save(self, data: Optional[Dict[str, dict]] = None):
        filepath = self.basedir / WEB_ACCOUNTS_FILE
        tmp_path = None
        payload = self._data if data is None else data
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=filepath.parent,
                prefix=f".{filepath.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
            try:
                tmp_path.chmod(0o600)
            except OSError:
                pass
            tmp_path.replace(filepath)
        except Exception:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def get_all(self) -> Dict[str, dict]:
        return deepcopy(self._data)

    def get(self, account_id: str) -> Optional[dict]:
        data = self._data.get(account_id)
        return deepcopy(data) if data is not None else None

    def add(self, account_id: str, data: dict):
        next_data = deepcopy(self._data)
        next_data[account_id] = deepcopy(data)
        self._save(next_data)
        self._data = next_data

    def update(self, account_id: str, data: dict, new_account_id: Optional[str] = None) -> Optional[str]:
        if account_id not in self._data:
            return None

        target_id = new_account_id or account_id
        if target_id != account_id and target_id in self._data:
            return None

        account_data = deepcopy(self._data[account_id])
        for k, v in data.items():
            if v is None:
                account_data.pop(k, None)
            else:
                account_data[k] = deepcopy(v)

        next_data = deepcopy(self._data)
        if target_id != account_id:
            del next_data[account_id]
        next_data[target_id] = account_data
        self._save(next_data)
        self._data = next_data
        return target_id

    def delete(self, account_id: str):
        if account_id in self._data:
            next_data = deepcopy(self._data)
            del next_data[account_id]
            self._save(next_data)
            self._data = next_data

    def _get_account_token(self, data: dict) -> str:
        encrypted_token = data.get("encrypted_token")
        return decrypt_token(encrypted_token, self.basedir) if encrypted_token else ""

    def _get_account_user_id(self, data: dict) -> str:
        user_id = data.get("user_id") or data.get("userid") or ""
        if isinstance(user_id, bool) or user_id is None:
            return ""
        if isinstance(user_id, int):
            user_id = str(user_id)
        if not isinstance(user_id, str):
            return ""
        return user_id.strip()

    def _to_emby_account(self, data: dict) -> EmbyAccount:
        account_dict = {
            "url": data["url"],
            "username": data["username"],
            "time": data.get("time", [300, 600]),
            "allow_multiple": data.get("allow_multiple", True),
            "allow_stream": data.get("allow_stream", False),
            "useragent": data.get("useragent"),
            "client": data.get("client"),
            "client_version": data.get("client_version"),
            "device": data.get("device"),
            "device_id": data.get("device_id"),
            "use_proxy": data.get("use_proxy", True),
            "play_id": data.get("play_id"),
            "enabled": data.get("enabled", True),
            "interval_days": data.get("interval_days"),
            "time_range": data.get("time_range"),
        }
        if data.get("name"):
            account_dict["name"] = data["name"]
        account_dict = {k: v for k, v in account_dict.items() if v is not None}
        return EmbyAccount(**account_dict)

    def to_emby_accounts(self) -> List[EmbyAccount]:
        """Convert web accounts to EmbyAccount objects for the scheduler."""
        accounts = []
        for aid, data in self._data.items():
            try:
                accounts.append(self._to_emby_account(data))
            except Exception as e:
                logger.error(f"Failed to convert web account {aid}: {e}")
        return accounts


class SchedulerBridge:
    """Bridges FastAPI with the existing embykeeper scheduling infrastructure."""

    def __init__(self):
        self.emby_manager = None
        self.web_accounts: WebAccountData = None
        self._base_emby_accounts: List[EmbyAccount] = []
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._account_status: Dict[str, dict] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._initialized = False

    def _record_status(self, account_id: str, **fields):
        existing = self._account_status.setdefault(account_id, {})
        existing.update(fields)

    def _get_running_task(self, account_id: str) -> Optional[asyncio.Task]:
        task = self._running_tasks.get(account_id)
        if task and task.done():
            self._running_tasks.pop(account_id, None)
            return None
        return task

    def _cancel_running_task(self, account_id: str):
        task = self._running_tasks.pop(account_id, None)
        if task and not task.done():
            task.cancel()

    def _cache_account_credentials(self, data: dict):
        try:
            token = self.web_accounts._get_account_token(data)
        except Exception as e:
            logger.warning(f"Skipping invalid cached Emby credentials: {type(e).__name__}")
            return
        if not token:
            return

        from embykeeper.cache import cache as credential_cache

        cache_data = {"token": token}
        user_id = self.web_accounts._get_account_user_id(data)
        if user_id:
            cache_data["userid"] = user_id
        cache_key = self._account_credential_cache_key(data)
        if not cache_key:
            return
        try:
            credential_cache.set(cache_key, cache_data)
        except Exception as e:
            logger.warning(f"Failed to cache Emby credentials: {type(e).__name__}")

    def _account_credential_cache_key(self, data: dict) -> Optional[str]:
        from urllib.parse import urlparse

        url = data.get("url")
        username = data.get("username")
        if not url or not username:
            return None
        hostname = urlparse(url).hostname
        if not hostname:
            return None
        return f"emby.credential.{hostname}.{username}"

    def _clear_account_credentials(self, data: dict):
        from embykeeper.cache import cache as credential_cache

        try:
            cache_key = self._account_credential_cache_key(data)
            if cache_key:
                credential_cache.delete(cache_key)
        except Exception as e:
            logger.warning(f"Failed to clear old Emby credential cache: {type(e).__name__}")

    def _remember_user_id(self, account_id: str, account_data: dict, emby):
        user_id = getattr(emby, "user_id", None)
        if not user_id or account_data.get("user_id") == user_id:
            return

        try:
            updated_id = self.web_accounts.update(account_id, {"user_id": user_id})
        except Exception as e:
            logger.warning(f"Failed to remember Emby user id for {account_id}: {type(e).__name__}")
            return
        if updated_id:
            account_data["user_id"] = user_id
            self._cache_account_credentials(account_data)

    async def initialize(self, basedir: Path):
        """Initialize the scheduler bridge on app startup."""
        if self._initialized or self._scheduler_task or self.emby_manager:
            await self.shutdown()

        # Initialize web accounts store
        self.web_accounts = WebAccountData(basedir)

        # Load existing config
        config.basedir = basedir
        config_file = basedir / "config.toml"
        if config_file.is_file():
            loaded = await config.reload_conf(config_file)
        else:
            loaded = False
        if not loaded:
            logger.warning("No valid config loaded; using defaults for API-managed accounts.")
            config.set(Config())
        if config._cache.emby is None:
            config.set(config._cache.model_copy(update={"emby": EmbyConfig()}), preserve_conf_file=True)
        self._base_emby_accounts = list(config._cache.emby.account or [])

        # Merge web-managed accounts into the config
        self._merge_accounts()

        # Create EmbyManager
        from embykeeper.emby.main import EmbyManager

        self.emby_manager = EmbyManager()

        # Start scheduled tasks in background (schedule_all blocks forever)
        self._scheduler_task = asyncio.create_task(self.emby_manager.schedule_all(instant=False))

        self._initialized = True
        logger.info("Scheduler bridge initialized.")

    def _merge_accounts(self):
        """Merge web-managed accounts into the active config."""
        if not config._cache:
            return

        web_accounts = self.web_accounts.to_emby_accounts()

        # Populate credential cache so the scheduler's Emby objects can find tokens
        for data in self.web_accounts.get_all().values():
            self._cache_account_credentials(data)

        # Combine original CLI accounts + current web accounts
        all_accounts = self._base_emby_accounts + web_accounts

        # Update config
        emby_config = config._cache.emby or EmbyConfig()
        new_config = config._cache.model_copy(
            update={"emby": emby_config.model_copy(update={"account": all_accounts})}
        )
        config.set(new_config, preserve_conf_file=True)

    def add_account(self, account_id: str, data: dict):
        """Add a new account via the web API."""
        self.web_accounts.add(account_id, data)
        self._merge_accounts()

    def update_account(
        self, account_id: str, data: dict, new_account_id: Optional[str] = None
    ) -> Optional[str]:
        """Update an existing account via the web API."""
        old_data = self.web_accounts.get(account_id)
        updated_id = self.web_accounts.update(account_id, data, new_account_id)
        if updated_id:
            if old_data:
                self._clear_account_credentials(old_data)
            self._cancel_running_task(account_id)
            if updated_id != account_id and account_id in self._account_status:
                self._account_status[updated_id] = self._account_status.pop(account_id)
            self._merge_accounts()
        return updated_id

    def delete_account(self, account_id: str):
        """Delete an account via the web API."""
        old_data = self.web_accounts.get(account_id)
        self.web_accounts.delete(account_id)
        if old_data:
            self._clear_account_credentials(old_data)
        self._cancel_running_task(account_id)
        self._account_status.pop(account_id, None)
        self._merge_accounts()

    def _prepare_emby(self, account_data: dict):
        from embykeeper.emby.api import Emby

        account = self.web_accounts._to_emby_account(account_data)
        emby = Emby(account)
        emby.set_credentials(
            self.web_accounts._get_account_token(account_data),
            self.web_accounts._get_account_user_id(account_data),
        )
        return emby, account

    async def _authenticate_emby(self, emby) -> bool:
        return await emby.authenticate_with_token()

    async def trigger_watch(self, account_id: str) -> dict:
        """Trigger an immediate watch for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {"error": "Account not found"}
        existing_task = self._get_running_task(account_id)
        if existing_task and not existing_task.done():
            return {
                "run_id": "",
                "status": "running",
                "message": "Watch task already running",
            }

        from embykeeper.runinfo import RunContext, RunStatus

        ctx = RunContext.prepare(description=f"Manual watch: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        async def run_watch():
            from embykeeper.emby.api import EmbyError

            now = datetime.now(timezone.utc)
            try:
                emby, account = self._prepare_emby(account_data)
                if not await self._authenticate_emby(emby):
                    ctx.finish(RunStatus.FAIL, "Token authentication failed")
                    self._record_status(
                        account_id, last_watch_time=now, last_watch_status="auth_failed", is_online=False
                    )
                    return
                self._remember_user_id(account_id, account_data, emby)
                if account.play_id:
                    item = await emby.get_item(account.play_id)
                    if not item or "Id" not in item:
                        ctx.finish(RunStatus.FAIL, "Video item not found")
                        self._record_status(account_id, last_watch_time=now, last_watch_status="no_video")
                        return
                    emby.items[item["Id"]] = item
                else:
                    await emby.load_main_page()
                    if not emby.items:
                        ctx.finish(RunStatus.FAIL, "No playable video found")
                        self._record_status(account_id, last_watch_time=now, last_watch_status="no_video")
                        return
                if await emby.watch():
                    ctx.finish(RunStatus.SUCCESS, "Watch successful")
                    self._record_status(
                        account_id, last_watch_time=now, last_watch_status="success", is_online=True
                    )
                else:
                    ctx.finish(RunStatus.FAIL, "Watch failed")
                    self._record_status(account_id, last_watch_time=now, last_watch_status="failed")
            except asyncio.CancelledError:
                ctx.finish(RunStatus.CANCELLED, "Watch task cancelled")
                raise
            except EmbyError as e:
                ctx.finish(RunStatus.FAIL, str(e))
                self._record_status(account_id, last_watch_time=now, last_watch_status="failed")
            except Exception as e:
                ctx.finish(RunStatus.ERROR, str(e))
                self._record_status(account_id, last_watch_time=now, last_watch_status="error")

        task = asyncio.create_task(run_watch(), name=f"watch-{account_id}")
        self._running_tasks[account_id] = task

        def cleanup(done_task: asyncio.Task):
            if self._running_tasks.get(account_id) is done_task:
                self._running_tasks.pop(account_id, None)

        task.add_done_callback(cleanup)

        return {"run_id": ctx.id, "status": "started", "message": "Watch task started"}

    async def trigger_watch_many(self, unified_only: bool = False) -> dict:
        """Trigger immediate watch tasks for enabled web-managed accounts."""
        run_ids = []
        for account_id, data in self.web_accounts.get_all().items():
            if not data.get("enabled", True):
                continue
            if unified_only and (data.get("time_range") or data.get("interval_days")):
                continue
            result = await self.trigger_watch(account_id)
            if result.get("run_id"):
                run_ids.append(result["run_id"])

        return {
            "run_id": run_ids[0] if run_ids else "",
            "status": "started" if run_ids else "skipped",
            "message": f"Started {len(run_ids)} watch task(s)",
        }

    async def trigger_login(self, account_id: str) -> dict:
        """Trigger an immediate login test for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {"error": "Account not found"}

        from embykeeper.runinfo import RunContext, RunStatus

        ctx = RunContext.prepare(description=f"Login test: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        now = datetime.now(timezone.utc)
        try:
            emby, _ = self._prepare_emby(account_data)
            if await self._authenticate_emby(emby):
                self._remember_user_id(account_id, account_data, emby)
                ctx.finish(RunStatus.SUCCESS, "Token authentication successful")
                self._record_status(account_id, last_login_time=now, is_online=True)
                return {"run_id": ctx.id, "status": "success", "message": "Token authentication successful"}
            else:
                ctx.finish(RunStatus.FAIL, "Token authentication failed")
                self._record_status(account_id, last_login_time=now, is_online=False)
                return {"run_id": ctx.id, "status": "failed", "message": "Token authentication failed"}
        except Exception as e:
            ctx.finish(RunStatus.ERROR, f"Login error: {e}")
            self._record_status(account_id, last_login_time=now, is_online=False)
            return {"run_id": ctx.id, "status": "error", "message": str(e)}

    def get_account_status(self, account_id: str) -> dict:
        """Get runtime status for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {}

        manager_running = getattr(self.emby_manager, "_running", set()) if self.emby_manager else set()
        is_running = self._get_running_task(account_id) is not None or account_id in manager_running
        has_token = bool(account_data.get("encrypted_token"))
        recorded = self._account_status.get(account_id, {})
        next_schedule_time = None
        if self.emby_manager:
            scheduler = getattr(self.emby_manager, "_schedulers", {}).get(account_id)
            if not scheduler and not (account_data.get("time_range") or account_data.get("interval_days")):
                scheduler = getattr(self.emby_manager, "_schedulers", {}).get("unified")
            if scheduler:
                try:
                    next_schedule_time = getattr(scheduler, "_next_time", None) or scheduler.next_time
                except Exception as e:
                    logger.warning(f"Failed to read next schedule time for {account_id}: {type(e).__name__}")

        return {
            "has_token": has_token,
            "is_online": recorded.get("is_online"),
            "is_running": is_running,
            "last_login_time": recorded.get("last_login_time"),
            "last_watch_time": recorded.get("last_watch_time"),
            "last_watch_status": recorded.get("last_watch_status"),
            "next_schedule_time": next_schedule_time,
        }

    @staticmethod
    def _format_interval_days(scheduler) -> Optional[str]:
        try:
            days = getattr(scheduler, "days", None)
            if days is None:
                return None
            if isinstance(days, (list, tuple)):
                if len(days) >= 2:
                    return f"<{days[0]},{days[1]}>"
                if len(days) == 1:
                    return str(days[0])
                return None
            return str(days)
        except Exception as e:
            logger.warning(f"Failed to format schedule interval: {type(e).__name__}")
            return None

    @staticmethod
    def _format_time_range(scheduler) -> Optional[str]:
        try:
            start_time = getattr(scheduler, "start_time", None)
            if not start_time:
                return None
            end_time = getattr(scheduler, "end_time", None) or start_time
            start = start_time.strftime("%H:%M") if hasattr(start_time, "strftime") else str(start_time)
            end = end_time.strftime("%H:%M") if hasattr(end_time, "strftime") else str(end_time)
            return f"<{start},{end}>" if start != end else start
        except Exception as e:
            logger.warning(f"Failed to format schedule time range: {type(e).__name__}")
            return None

    def get_schedule_info(self) -> List[dict]:
        """Get schedule info for all scheduled tasks."""
        schedules = []
        if not self.emby_manager:
            return schedules

        for account_spec, scheduler in self.emby_manager._schedulers.items():
            schedule_id = getattr(scheduler, "sid", None) or account_spec
            interval_days = self._format_interval_days(scheduler)
            time_range = self._format_time_range(scheduler)

            try:
                next_time = getattr(scheduler, "_next_time", None) or scheduler.next_time
            except Exception as e:
                logger.warning(f"Failed to read next schedule time for {account_spec}: {type(e).__name__}")
                next_time = None

            manager_running = getattr(self.emby_manager, "_running", set())
            manager_tasks = getattr(self.emby_manager, "_tasks", {})
            running_task = manager_tasks.get(account_spec)
            task_is_running = bool(running_task and not running_task.done())

            account_data = self.web_accounts.get(account_spec) if self.web_accounts else None
            enabled = account_data.get("enabled", True) if account_data else True

            schedules.append(
                {
                    "id": schedule_id,
                    "account_spec": account_spec,
                    "interval_days": interval_days,
                    "time_range": time_range,
                    "next_time": next_time,
                    "is_running": account_spec in manager_running or task_is_running,
                    "enabled": enabled,
                }
            )
        return schedules

    async def shutdown(self):
        """Cleanup on shutdown."""
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except (asyncio.CancelledError, Exception):
                pass

        tasks = list(self._running_tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self.emby_manager:
            try:
                await self.emby_manager.shutdown()
            except Exception:
                pass

        self.emby_manager = None
        self.web_accounts = None
        self._base_emby_accounts = []
        self._running_tasks = {}
        self._account_status = {}
        self._scheduler_task = None
        self._initialized = False


# Global singleton
bridge = SchedulerBridge()
