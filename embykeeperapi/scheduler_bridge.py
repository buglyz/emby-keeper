import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from embykeeper.config import config
from embykeeper.schema import EmbyAccount

from .crypto import decrypt_token


logger = logger.bind(scheme="embykeeperapi")


# Web-managed account data store
WEB_ACCOUNTS_FILE = "web_accounts.json"


class WebAccountData:
    """Manages Emby accounts created via the web UI."""

    def __init__(self, basedir: Path):
        self.basedir = basedir
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self):
        filepath = self.basedir / WEB_ACCOUNTS_FILE
        if filepath.is_file():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("Web accounts file corrupted, starting fresh.")
                self._data = {}

    def _save(self):
        filepath = self.basedir / WEB_ACCOUNTS_FILE
        tmp_path = filepath.with_suffix(f"{filepath.suffix}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(filepath)

    def get_all(self) -> Dict[str, dict]:
        return self._data.copy()

    def get(self, account_id: str) -> Optional[dict]:
        return self._data.get(account_id)

    def add(self, account_id: str, data: dict):
        self._data[account_id] = data
        self._save()

    def update(self, account_id: str, data: dict, new_account_id: Optional[str] = None) -> Optional[str]:
        if account_id not in self._data:
            return None

        target_id = new_account_id or account_id
        if target_id != account_id and target_id in self._data:
            return None

        account_data = self._data[account_id].copy()
        for k, v in data.items():
            if v is None:
                account_data.pop(k, None)
            else:
                account_data[k] = v

        if target_id != account_id:
            del self._data[account_id]
        self._data[target_id] = account_data
        self._save()
        return target_id

    def delete(self, account_id: str):
        if account_id in self._data:
            del self._data[account_id]
            self._save()

    def _get_account_token(self, data: dict) -> str:
        encrypted_token = data.get("encrypted_token")
        return decrypt_token(encrypted_token, self.basedir) if encrypted_token else ""

    def _to_emby_account(self, data: dict) -> EmbyAccount:
        account_dict = {
            "url": data["url"],
            "username": data["username"],
            "password": "",
            "name": data.get("name"),
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
        self._initialized = False

    async def initialize(self, basedir: Path):
        """Initialize the scheduler bridge on app startup."""
        # Initialize web accounts store
        self.web_accounts = WebAccountData(basedir)

        # Load existing config
        config.basedir = basedir
        await config.reload_conf()
        self._base_emby_accounts = list(config.emby.account)

        # Merge web-managed accounts into the config
        self._merge_accounts()

        # Create EmbyManager
        from embykeeper.emby.main import EmbyManager
        self.emby_manager = EmbyManager()

        # Start scheduled tasks for all accounts
        await self.emby_manager.schedule_all(instant=False)

        self._initialized = True
        logger.info("Scheduler bridge initialized.")

    def _merge_accounts(self):
        """Merge web-managed accounts into the active config."""
        if not config._cache:
            return

        web_accounts = self.web_accounts.to_emby_accounts()

        # Combine original CLI accounts + current web accounts
        all_accounts = self._base_emby_accounts + web_accounts

        # Update config
        new_config = config._cache.model_copy(update={
            "emby": config._cache.emby.model_copy(update={"account": all_accounts})
        })
        config.set(new_config)

    def add_account(self, account_id: str, data: dict):
        """Add a new account via the web API."""
        self.web_accounts.add(account_id, data)
        self._merge_accounts()

    def update_account(self, account_id: str, data: dict, new_account_id: Optional[str] = None) -> Optional[str]:
        """Update an existing account via the web API."""
        updated_id = self.web_accounts.update(account_id, data, new_account_id)
        if updated_id:
            self._merge_accounts()
        return updated_id

    def delete_account(self, account_id: str):
        """Delete an account via the web API."""
        self.web_accounts.delete(account_id)
        self._merge_accounts()

    def _prepare_emby(self, account_data: dict):
        from embykeeper.emby.api import Emby

        account = self.web_accounts._to_emby_account(account_data)
        emby = Emby(account)
        emby.set_credentials(self.web_accounts._get_account_token(account_data))
        return emby, account

    async def _authenticate_emby(self, emby) -> bool:
        return await emby.authenticate_with_token()

    async def trigger_watch(self, account_id: str) -> dict:
        """Trigger an immediate watch for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {"error": "Account not found"}

        from embykeeper.runinfo import RunContext, RunStatus

        ctx = RunContext.prepare(description=f"Manual watch: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        async def run_watch():
            from embykeeper.emby.api import EmbyError

            emby, account = self._prepare_emby(account_data)
            try:
                if not await self._authenticate_emby(emby):
                    ctx.finish(RunStatus.FAIL, "Token authentication failed")
                    return
                if account.play_id:
                    item = await emby.get_item(account.play_id)
                    if not item or "Id" not in item:
                        ctx.finish(RunStatus.FAIL, "Video item not found")
                        return
                    emby.items[item["Id"]] = item
                else:
                    await emby.load_main_page()
                    if not emby.items:
                        ctx.finish(RunStatus.FAIL, "No playable video found")
                        return
                if await emby.watch():
                    ctx.finish(RunStatus.SUCCESS, "Watch successful")
                else:
                    ctx.finish(RunStatus.FAIL, "Watch failed")
            except EmbyError as e:
                ctx.finish(RunStatus.FAIL, str(e))
            except Exception as e:
                ctx.finish(RunStatus.ERROR, str(e))

        task = asyncio.create_task(run_watch(), name=f"watch-{account_id}")
        self._running_tasks[account_id] = task
        task.add_done_callback(lambda _: self._running_tasks.pop(account_id, None))

        return {"run_id": ctx.id, "status": "started"}

    async def trigger_login(self, account_id: str) -> dict:
        """Trigger an immediate login test for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {"error": "Account not found"}

        from embykeeper.runinfo import RunContext, RunStatus

        ctx = RunContext.prepare(description=f"Login test: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        emby, _ = self._prepare_emby(account_data)
        try:
            if await self._authenticate_emby(emby):
                ctx.finish(RunStatus.SUCCESS, "Token authentication successful")
                return {"run_id": ctx.id, "status": "success", "message": "Token authentication successful"}
            else:
                ctx.finish(RunStatus.FAIL, "Token authentication failed")
                return {"run_id": ctx.id, "status": "failed", "message": "Token authentication failed"}
        except Exception as e:
            ctx.finish(RunStatus.ERROR, f"Login error: {e}")
            return {"run_id": ctx.id, "status": "error", "message": str(e)}

    async def trigger_checkin(self, account_id: str) -> dict:
        """Trigger a check-in for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {"error": "Account not found"}

        plugin_id = account_data.get("checkin_plugin_id")
        if not plugin_id:
            return {"error": "No check-in plugin configured for this account"}

        from embykeeper.runinfo import RunContext, RunStatus

        ctx = RunContext.prepare(description=f"Check-in: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        emby, _ = self._prepare_emby(account_data)
        try:
            if not await self._authenticate_emby(emby):
                ctx.finish(RunStatus.FAIL, "Token authentication failed for check-in")
                return {"run_id": ctx.id, "status": "failed", "message": "Token authentication failed"}

            resp = await emby._request(
                "POST",
                f"/Plugins/{plugin_id}/CheckIn",
            )

            if resp.ok:
                ctx.finish(RunStatus.SUCCESS, "Check-in successful")
                return {"run_id": ctx.id, "status": "success", "message": "Check-in successful"}
            else:
                ctx.finish(RunStatus.FAIL, f"Check-in failed: HTTP {resp.status_code}")
                return {"run_id": ctx.id, "status": "failed", "message": f"Check-in failed: HTTP {resp.status_code}"}
        except Exception as e:
            ctx.finish(RunStatus.ERROR, f"Check-in error: {e}")
            return {"run_id": ctx.id, "status": "error", "message": str(e)}

    def get_account_status(self, account_id: str) -> dict:
        """Get runtime status for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {}

        is_running = account_id in self._running_tasks
        has_token = bool(account_data.get("encrypted_token"))

        return {
            "has_token": has_token,
            "is_online": has_token,  # Token validity is checked by the login test action.
            "is_running": is_running,
            "last_login_time": None,
            "last_watch_time": None,
        }

    def get_schedule_info(self) -> List[dict]:
        """Get schedule info for all scheduled tasks."""
        schedules = []
        if self.emby_manager:
            for sid, scheduler in self.emby_manager._schedulers.items():
                schedules.append({
                    "id": sid,
                    "account_spec": sid.replace("emby.watch.", "") if sid.startswith("emby.watch.") else sid,
                    "interval_days": str(scheduler.days) if hasattr(scheduler, "days") else None,
                    "time_range": f"{scheduler.start_time}-{scheduler.end_time}" if hasattr(scheduler, "start_time") else None,
                    "next_time": None,
                    "is_running": sid in self.emby_manager._running,
                    "enabled": True,
                })
        return schedules

    async def shutdown(self):
        """Cleanup on shutdown."""
        tasks = list(self._running_tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


# Global singleton
bridge = SchedulerBridge()