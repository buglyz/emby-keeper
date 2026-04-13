import asyncio
from . import BotCheckin

from pyrogram.types import Message
from pyrogram.errors import ChatWriteForbidden

__ignore__ = True


class EPubGroupCheckin(BotCheckin):
    name = "EPub 电子书库群组签到"
    chat_name = "libhsulife"
    bot_username = "zhruonanbot"
    bot_use_captcha = False

    async def send_checkin(self, retry=False):
        try:
            msg = await self.send("签到")
            if msg:
                self.mid = msg.id
        except ChatWriteForbidden:
            self.log.info("被禁言, 准备 2 分钟后重新签到.")
            await asyncio.sleep(120)
            await self.retry()

    async def message_handler(self, client, message: Message, type=None):
        mid = getattr(self, "mid", None)
        if mid and message.reply_to_message_id == mid:
            return await super().message_handler(client, message, type=type)
