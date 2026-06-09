from __future__ import annotations

import asyncio

from loguru import logger

from embykeeper.utils import format_exception_summary

logger = logger.bind(scheme="embykeeperapi")


class TelegramAutomationRuntime:
    """Owns Telegram check-in and registrar managers for the WebUI process."""

    def __init__(self):
        self.checkiner_manager = None
        self.registrar_manager = None
        self._checkiner_task: asyncio.Task | None = None
        self._registrar_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._started = False

    def _task_done(self, task_name: str, attr_name: str):
        def cleanup(task: asyncio.Task):
            if getattr(self, attr_name, None) is task:
                setattr(self, attr_name, None)
            if task.cancelled():
                return
            try:
                task.result()
            except Exception as e:
                logger.warning(
                    f"Telegram {task_name} runtime stopped unexpectedly: {format_exception_summary(e)}"
                )

        return cleanup

    async def start(self):
        async with self._lock:
            if self._started:
                return
            self._started = True
            await self._start_locked()

    async def restart_if_started(self):
        async with self._lock:
            if not self._started:
                return
            await self._shutdown_locked(clear_started=False)
            await self._start_locked()

    async def shutdown(self):
        async with self._lock:
            await self._shutdown_locked(clear_started=True)

    async def _start_locked(self):
        from embykeeper.config import config

        if not config._cache:
            logger.warning("Config is not loaded; WebUI Telegram automation runtime is disabled.")
            return

        try:
            from embykeeper.telegram.checkin_main import CheckinerManager
        except ImportError as e:
            logger.warning(
                "Telegram check-in dependencies are not installed; WebUI automation check-in is disabled."
            )
            logger.debug(f"Telegram check-in import failed: {format_exception_summary(e)}")
        else:
            try:
                self.checkiner_manager = CheckinerManager()
                self._checkiner_task = asyncio.create_task(
                    self.checkiner_manager.schedule_all(), name="telegram-checkiner-runtime"
                )
                self._checkiner_task.add_done_callback(
                    self._task_done("check-in", "_checkiner_task")
                )
            except Exception as e:
                self.checkiner_manager = None
                logger.warning(
                    f"Failed to start Telegram check-in runtime: {format_exception_summary(e)}"
                )

        try:
            from embykeeper.telegram.registrar_main import RegisterManager
        except ImportError as e:
            logger.warning(
                "Telegram registrar dependencies are not installed; WebUI automation registrar is disabled."
            )
            logger.debug(f"Telegram registrar import failed: {format_exception_summary(e)}")
        else:
            try:
                self.registrar_manager = RegisterManager()
                self._registrar_task = asyncio.create_task(
                    self.registrar_manager.start(), name="telegram-registrar-runtime"
                )
                self._registrar_task.add_done_callback(
                    self._task_done("registrar", "_registrar_task")
                )
            except Exception as e:
                self.registrar_manager = None
                logger.warning(
                    f"Failed to start Telegram registrar runtime: {format_exception_summary(e)}"
                )

    async def _shutdown_locked(self, *, clear_started: bool):
        managers = [self.checkiner_manager, self.registrar_manager]
        for manager in managers:
            if not manager:
                continue
            try:
                await manager.shutdown()
            except Exception as e:
                logger.warning(
                    f"Failed to shutdown Telegram automation manager: {format_exception_summary(e)}"
                )

        tasks = [task for task in (self._checkiner_task, self._registrar_task) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self.checkiner_manager = None
        self.registrar_manager = None
        self._checkiner_task = None
        self._registrar_task = None
        if clear_started:
            self._started = False


automation_runtime = TelegramAutomationRuntime()
