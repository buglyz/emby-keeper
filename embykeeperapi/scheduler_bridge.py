import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from embykeeper.config import config
from embykeeper.schema import EmbyAccount, EmbyConfig, Config
from embykeeper.cache import cache

from .crypto import encrypt_token, decrypt_token


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
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get_all(self) -> Dict[str, dict]:
        return self._data.copy()

    def get(self, account_id: str) -> Optional[dict]:
        return self._data.get(account_id)

    def add(self, account_id: str, data: dict):
        self._data[account_id] = data
        self._save()

    def update(self, account_id: str, data: dict):
        if account_id in self._data:
            # Merge updates
            for k, v in data.items():
                if v is not None:
                    self._data[account_id][k] = v
            self._save()

    def delete(self, account_id: str):
        if account_id in self._data:
            del self._data[account_id]
            self._save()

    def to_emby_accounts(self) -> List[EmbyAccount]:
        """Convert web accounts to EmbyAccount objects for the scheduler."""
        accounts = []
        for aid, data in self._data.items():
            try:
                # Decrypt the stored token for runtime use
                encrypted_token = data.get("encrypted_token")
                password = ""
                if encrypted_token:
                    password = decrypt_token(encrypted_token, self.basedir)

                account_dict = {
                    "url": data["url"],
                    "username": data["username"],
                    "password": password,
                    "name": data.get("name"),
                    "time": data.get("time", [300, 600]),
                    "allow_multiple": data.get("allow_multiple", True),
                    "allow_stream": data.get("allow_stream", False),
                    "use_proxy": data.get("use_proxy", True),
                    "play_id": data.get("play_id"),
                    "enabled": data.get("enabled", True),
                    "interval_days": data.get("interval_days"),
                    "time_range": data.get("time_range"),
                }

                # Remove None values that EmbyAccount doesn't accept
                account_dict = {k: v for k, v in account_dict.items() if v is not None}

                accounts.append(EmbyAccount(**account_dict))
            except Exception as e:
                logger.error(f"Failed to convert web account {aid}: {e}")
        return accounts


class SchedulerBridge:
    """Bridges FastAPI with the existing embykeeper scheduling infrastructure."""

    def __init__(self):
        self.emby_manager = None
        self.web_accounts: WebAccountData = None
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._initialized = False

    async def initialize(self, basedir: Path):
        """Initialize the scheduler bridge on app startup."""
        # Initialize web accounts store
        self.web_accounts = WebAccountData(basedir)

        # Load existing config
        config.basedir = basedir
        await config.reload_conf()

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

        # Combine CLI accounts + web accounts
        existing_accounts = list(config.emby.account)
        all_accounts = existing_accounts + web_accounts

        # Update config
        new_config = config._cache.model_copy(update={
            "emby": config._cache.emby.model_copy(update={"account": all_accounts})
        })
        config.set(new_config)

    def add_account(self, account_id: str, data: dict):
        """Add a new account via the web API."""
        self.web_accounts.add(account_id, data)
        self._merge_accounts()

    def update_account(self, account_id: str, data: dict):
        """Update an existing account via the web API."""
        self.web_accounts.update(account_id, data)
        self._merge_accounts()

    def delete_account(self, account_id: str):
        """Delete an account via the web API."""
        self.web_accounts.delete(account_id)
        self._merge_accounts()

    async def trigger_watch(self, account_id: str) -> dict:
        """Trigger an immediate watch for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {"error": "Account not found"}

        accounts = self.web_accounts.to_emby_accounts()
        account = None
        for a in accounts:
            spec = f"{a.username}@{a.name or a.url.host}"
            if spec == account_id:
                account = a
                break

        if not account:
            return {"error": "Account not found in converted list"}

        from embykeeper.runinfo import RunContext, RunStatus
        ctx = RunContext.prepare(description=f"Manual watch: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        task = asyncio.create_task(
            self.emby_manager._watch_main([account], instant=True),
            name=f"watch-{account_id}",
        )
        self._running_tasks[account_id] = task

        return {"run_id": ctx.id, "status": "started"}

    async def trigger_login(self, account_id: str) -> dict:
        """Trigger an immediate login test for a specific account."""
        account_data = self.web_accounts.get(account_id)
        if not account_data:
            return {"error": "Account not found"}

        accounts = self.web_accounts.to_emby_accounts()
        account = None
        for a in accounts:
            spec = f"{a.username}@{a.name or a.url.host}"
            if spec == account_id:
                account = a
                break

        if not account:
            return {"error": "Account not found"}

        from embykeeper.emby.api import Emby
        from embykeeper.runinfo import RunContext, RunStatus

        ctx = RunContext.prepare(description=f"Login test: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        emby = Emby(account)
        try:
            result = await emby.login()
            if result:
                ctx.finish(RunStatus.SUCCESS, "Login successful")
                return {"run_id": ctx.id, "status": "success", "message": "Login successful"}
            else:
                ctx.finish(RunStatus.FAIL, "Login failed")
                return {"run_id": ctx.id, "status": "failed", "message": "Login failed - invalid credentials"}
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

        accounts = self.web_accounts.to_emby_accounts()
        account = None
        for a in accounts:
            spec = f"{a.username}@{a.name or a.url.host}"
            if spec == account_id:
                account = a
                break

        if not account:
            return {"error": "Account not found"}

        from embykeeper.emby.api import Emby
        from embykeeper.runinfo import RunContext, RunStatus

        ctx = RunContext.prepare(description=f"Check-in: {account_id}")
        ctx.start(RunStatus.INITIALIZING)

        emby = Emby(account)
        try:
            if not await emby.login():
                ctx.finish(RunStatus.FAIL, "Login failed for check-in")
                return {"run_id": ctx.id, "status": "failed", "message": "Login failed"}

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

        hostname = account_data.get("url", "")
        username = account_data.get("username", "")

        # Check credential cache for login/watch status
        credential = cache.get(f"emby.credential.{hostname}.{username}", {})
        env = cache.get(f"emby.env.{hostname}.{username}", {})

        is_running = account_id in self._running_tasks
        has_token = bool(credential.get("token"))

        return {
            "has_token": has_token,
            "is_online": has_token,  # Approximate: if we have a cached token, likely online
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
        for task in self._running_tasks.values():
            task.cancel()
        for task in self._running_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass


# Global singleton
bridge = SchedulerBridge()