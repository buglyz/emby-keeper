from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Union

from loguru import logger
from pyrogram.errors import ChatWriteForbidden
from pyrogram.types import User

from embykeeper.config import config as app_config
from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.schema import TelegramAccount
from embykeeper.utils import show_exception, truncate_str

from ..link import Link
from ..pyrogram import Client
from ..session import ClientsSession

__ignore__ = True


class SmartMessager:
    """Lightweight SmartMessager compatibility used by legacy checkiners.

    The old project exposed SmartMessager as a schedulable auto-messager.  This
    fork only needs the one-off ``send()`` surface for checkiners such as
    ``pornfans_group``.
    """

    name: str = None
    chat_name: str = None
    additional_auth: List[str] = []
    max_length: int = 50
    extra_prompt: str = ""
    send_delay: int = 5

    def __init__(
        self,
        account: Union[TelegramAccount, Client],
        me: User = None,
        context: RunContext = None,
        config: dict = None,
    ):
        self.account = account
        self.ctx = context or RunContext.prepare(description=self.name or "智能发言")
        self.config = config or {}
        self.me = me
        if not self.me and isinstance(account, Client):
            self.me = account.me
        username = getattr(self.me, "full_name", None) or "unknown"
        self.log = self.ctx.bind_logger(logger.bind(scheme="telemessager", name=self.name, username=username))

    async def _with_error_handling(self, func):
        try:
            return await func()
        except Exception as e:
            try:
                nofail = app_config.nofail
            except RuntimeError:
                nofail = True
            if nofail:
                self.log.warning("智能发言失败, 已跳过.")
                show_exception(e, regular=False)
                self.ctx.finish(RunStatus.ERROR, "智能发言异常")
                return None
            raise

    async def init(self):
        return True

    @staticmethod
    def _chat_label(chat):
        return (
            getattr(chat, "full_name", None)
            or getattr(chat, "title", None)
            or getattr(chat, "username", None)
            or str(getattr(chat, "id", "unknown"))
        )

    async def _recent_context(self, tg: Client, chat_id) -> List[str]:
        context = []
        try:
            async for msg in tg.get_chat_history(chat_id, limit=20):
                text = str(getattr(msg, "caption", None) or getattr(msg, "text", None) or "").strip()
                if not text:
                    continue
                sender = getattr(getattr(msg, "from_user", None), "full_name", None)
                if sender:
                    text = f"{sender}说: {text}"
                context.append(truncate_str(text.replace("\n", " "), 120))
        except Exception as e:
            self.log.debug(f"读取近期群聊消息失败, 将仅使用基础提示生成发言: {type(e).__name__}")
        return list(reversed(context[-10:]))

    async def get_infer_prompt(self, tg: Client, chat, now: datetime = None):
        prompt = "我需要你在一个群聊中进行合理的简短回复."
        context = await self._recent_context(tg, chat.id)
        if context:
            prompt += "\n该群聊最近的消息如下, 从早到晚排列:\n"
            for item in context:
                prompt += f"- {item}\n"
        prompt += "\n其他信息:\n"
        prompt += f"- 我的用户名: {tg.me.full_name}\n"
        prompt += f"- 当前时间: {(now or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}\n"

        custom_prompt = self.config.get("prompt")
        if custom_prompt:
            prompt += f"\n{custom_prompt}\n"
        else:
            prompt += (
                "\n请直接输出一条中文回复, 不要解释, 不要包含 emoji, 不要 @ 其他人, "
                "不要包含自己的用户名. 如果此时不适合发言, 只输出 SKIP.\n"
            )
            extra_prompt = self.config.get("extra_prompt") or self.extra_prompt
            if extra_prompt:
                prompt += f"{extra_prompt}\n"
        return prompt

    async def _send(self, tg: Client, dummy: bool = False):
        chat = await tg.get_chat(self.chat_name)
        chat_label = self._chat_label(chat)
        log = self.ctx.bind_logger(self.log.bind(username=tg.me.full_name))

        prompt = await self.get_infer_prompt(tg, chat)
        answer, _ = await Link(tg).infer(prompt)
        answer = (answer or "").strip()
        if not answer:
            log.warning("智能推测发言内容失败, 将不发送消息.")
            return None
        if "SKIP" in answer.upper():
            log.info("智能推测此时不适合发言, 已跳过.")
            return None
        if self.max_length and len(answer) > self.max_length:
            log.info("智能推测发言内容过长, 已跳过.")
            return None

        if dummy:
            log.info(f'当前情况下在聊天 "{chat_label}" 中推断可发送内容为: {truncate_str(answer, 20)}')
            return None

        delay = self.config.get("send_delay", self.send_delay)
        if delay:
            await asyncio.sleep(max(0, int(delay)))
        try:
            message = await tg.send_message(chat.id, answer)
        except ChatWriteForbidden:
            log.warning("群组已禁言, 将不发送消息.")
            return None
        log.info(f'已向聊天 "{chat_label}" 发送: {truncate_str(answer, 20)}')
        return message

    async def send(self, dummy: bool = False):
        async def run():
            if not await self.init():
                self.ctx.finish(RunStatus.FAIL, "智能发言初始化失败")
                return None
            if isinstance(self.account, Client):
                return await self._send(self.account, dummy=dummy)
            async with ClientsSession([self.account]) as clients:
                async for _, tg in clients:
                    return await self._send(tg, dummy=dummy)
            return None

        return await self._with_error_handling(run)
