import asyncio
import random
from . import BotCheckin

from pyrogram.types import Message, InlineKeyboardMarkup
from pyrogram.raw.types.messages import BotCallbackAnswer

__ignore__ = True


class MiniSGKCheckin(BotCheckin):
    name = "迷你世界社工库"
    bot_username = "mnsjsgkbot"
    bot_checkin_cmd = "/sign"

    async def message_handler(self, client, message: Message):
        if (
            message.reply_markup
            and isinstance(message.reply_markup, InlineKeyboardMarkup)
            and message.reply_markup.inline_keyboard
        ):
            keys = [k.text for r in message.reply_markup.inline_keyboard for k in r]
            for k in keys:
                if "签到" in k:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    answer: BotCallbackAnswer = await message.click(k)
                    await self.on_text(Message(id=0), answer.message)
                    return
            else:
                self.log.warning(f"签到失败: 账户错误.")
                return await self.fail()
