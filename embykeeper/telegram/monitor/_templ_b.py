import asyncio
from datetime import datetime
import random
import re
from typing import List, Optional, Union

from cachetools import TTLCache
from loguru import logger
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.types import Message
from pyrogram.errors import RPCError
from cachetools import TTLCache
from pydantic import BaseModel, ValidationError

from embykeeper.runinfo import RunStatus
from embykeeper.utils import to_iterable, truncate_str

from . import Monitor

__ignore__ = True


class TemplateBMonitorConfig(BaseModel):
    name: str = None
    chat_name: Optional[Union[str, int]] = None  # 监控的群聊名称
    chat_follow_user: int = 10  # 需要等待 N 个用户发送相同内容方可回复
    chat_keyword: Union[str, List[str]] = []  # 仅当消息含有列表中的关键词时触发, 支持 regex
    chat_except_keyword: Union[str, List[str]] = []  # 消息含有列表中的关键词时不触发, 支持 regex
    chat_probability: float = 1.0  # 发信概率 (0最低, 1最高)
    chat_delay: int = 0  # 发信延迟 (秒)
    chat_max_length: int = 120  # 发送最大长度 (字符)
    trigger_interval: float = 120  # 每次触发的最低时间间隔 (秒)
    allow_same_user: bool = False  # 是否允许同一个人的消息


class TemplateBMonitor(Monitor):
    init_first = True
    additional_auth = ["prime"]

    async def init(self):
        try:
            self.t_config = TemplateBMonitorConfig.model_validate(self.config)
        except ValidationError as e:
            self.log.warning(f"初始化失败: 监控器自定义模板 B 的配置错误:\n{e}")
            return False
        self.name = self.t_config.name or "自定义"
        self.chat_name = self.t_config.chat_name
        self.chat_keyword = self.t_config.chat_keyword
        self.chat_except_keyword = self.t_config.chat_except_keyword
        self.chat_probability = self.t_config.chat_probability
        self.chat_delay = self.t_config.chat_delay
        self.chat_max_length = self.t_config.chat_max_length
        self.chat_follow_user = self.t_config.chat_follow_user
        self.trigger_interval = self.t_config.trigger_interval
        self.allow_same_user = self.t_config.allow_same_user

        self.lock = asyncio.Lock()
        self.chat_history = TTLCache(maxsize=2048, ttl=300)
        self.last_send = None
        self.log = logger.bind(scheme="telemonitor", name=self.name, username=self.client.me.full_name)
        return True

    async def message_handler(self, client: Client, message: Message):
        if not message.text:
            return
        if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return
        if len(message.text) > 50:
            return
        if message.text.startswith("/"):
            return
        if not message.from_user:
            return
        if message.from_user.is_bot:
            return
        for k in to_iterable(self.chat_except_keyword):
            if re.search(k, message.text, re.IGNORECASE):
                return
        if self.chat_keyword:
            for k in to_iterable(self.chat_keyword):
                if re.search(k, message.text, re.IGNORECASE):
                    break
            else:
                return
        if self.chat_max_length and len(message.text) > self.chat_max_length:
            return
        ident = (message.chat.id, message.text)
        async with self.lock:
            if ident not in self.chat_history:
                self.chat_history[ident] = {message.from_user.id} if not self.allow_same_user else 1
                return

            if self.allow_same_user:
                self.chat_history[ident] += 1
                count = self.chat_history[ident]
            else:
                self.chat_history[ident].add(message.from_user.id)
                count = len(self.chat_history[ident])

            if count == self.chat_follow_user:
                try:
                    chat_id, text = ident
                    if random.random() >= self.chat_probability:
                        return
                    if self.chat_delay and self.chat_delay > 0:
                        await asyncio.sleep(self.chat_delay)
                    if self.trigger_interval and self.trigger_interval > 0:
                        if self.last_send:
                            elapsed_time = (datetime.now() - self.last_send).total_seconds()
                            if self.last_send and (elapsed_time < self.trigger_interval):
                                await asyncio.sleep(self.trigger_interval - elapsed_time)
                    await self.client.send_message(chat_id, text)
                except RPCError as e:
                    self.log.warning(f"发送从众信息到群组 {message.chat.title} 失败: {e}.")
                else:
                    self.last_send = datetime.now()
                    self.log.info(f"已发送从众信息到群组 {message.chat.title}: {text}.")


def use(**kw):
    return type("TemplatedClass", (TemplateBMonitor,), kw)
