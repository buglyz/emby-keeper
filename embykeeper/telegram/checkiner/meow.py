import asyncio
import random
from pyrogram.types import Message
from pyrogram.errors import MessageIdInvalid

from ._templ_a import TemplateACheckin


class MeowCheckin(TemplateACheckin):
    name = "飞了个喵"
    bot_username = "gymeowfly_bot"

    async def message_handler(self, client, message: Message):
        if message.caption and "请先验证你不是机器人" in message.caption and message.reply_markup:
            keys = [k.text for r in message.reply_markup.inline_keyboard for k in r]
            for k in keys:
                if "我不是机器人" in k:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    try:
                        await message.click(k)
                    except TimeoutError:
                        self.log.debug(f"点击签到按钮无响应, 可能按钮未正确处理点击回复. 一般来说不影响签到.")
                    except MessageIdInvalid:
                        pass
                    return
            else:
                self.log.warning(f"签到失败: 账户错误.")
                return await self.fail()
        else:
            return await super().message_handler(client, message)
