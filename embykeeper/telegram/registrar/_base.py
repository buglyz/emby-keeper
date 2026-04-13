import asyncio
from abc import ABC, abstractmethod

from loguru import logger

from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.utils import show_exception
from embykeeper.config import config
from embykeeper.telegram.pyrogram import Client

__ignore__ = True

logger = logger.bind(scheme="teleregistrar")


class BaseBotRegister(ABC):
    """基础注册类."""

    name: str = None

    def __init__(
        self,
        client: Client,
        context: RunContext = None,
        retries=None,
        timeout=None,
        config: dict = {},
    ):
        self.client = client
        self.ctx = context or RunContext.prepare()

        self._retries = retries
        self._timeout = timeout

        self.config = config
        self.finished = asyncio.Event()  # 注册完成事件
        self.log = self.ctx.bind_logger(logger.bind(name=self.name, username=client.me.full_name))  # 日志组件

        self._task = None  # 主任务

    @property
    def retries(self):
        return self._retries or getattr(config, "register", {}).get("retries", 1)

    @property
    def timeout(self):
        return self._timeout or getattr(config, "register", {}).get("timeout", 120)

    async def _start(self):
        """注册器的入口函数的错误处理外壳."""
        try:
            self.client.stop_handlers.append(self.stop)
            self._task = asyncio.create_task(self.start())
            return await self._task
        except Exception as e:
            if config.nofail:
                self.log.warning(f"初始化异常错误, 注册器将停止.")
                show_exception(e, regular=False)
                return self.ctx.finish(RunStatus.ERROR, "异常错误")
            else:
                raise
        finally:
            if hasattr(self.client, "stop_handlers") and self.stop in self.client.stop_handlers:
                self.client.stop_handlers.remove(self.stop)
            self._task = None

    @abstractmethod
    async def start(self) -> RunContext:
        pass

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
