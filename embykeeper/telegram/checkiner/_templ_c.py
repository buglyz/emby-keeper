from datetime import datetime, timedelta
import random
import re
import string

from embykeeper.runinfo import RunStatus

from ..embyboss import EmbybossRegister
from . import BotCheckin

__ignore__ = True


class UniqueUsername(dict):
    """为同一个 Telegram 用户生成稳定的默认注册用户名."""

    def __getitem__(self, user):
        if user.id not in self:
            self[user.id] = self.get_unique(user)
        return dict.__getitem__(self, user.id)

    @staticmethod
    def get_unique(user):
        if user.username:
            unique = user.username
        else:
            unique = user.full_name.lower()
        unique = re.sub(r"[^A-Za-z0-9]", "", unique)
        random_bits = 10 - len(unique)
        if random_bits:
            random_bits = "".join(random.choice(string.digits) for _ in range(random_bits))
            unique = unique + random_bits
        return unique


class TemplateCCheckin(BotCheckin):
    bot_use_captcha = False
    unique_cache = UniqueUsername()

    def get_unique_name(self):
        unique_name = self.config.get("unique_name", None)
        if unique_name:
            return unique_name
        else:
            return self.__class__.unique_cache[self.client.me]

    async def start(self):
        random_code = "".join(random.choices(string.ascii_letters + string.digits, k=4))
        if await EmbybossRegister(self.client, self.log, self.get_unique_name(), random_code).run(
            self.bot_username
        ):
            self.log.bind(log=True).info(f"定时开注测试器成功注册机器人 {self.bot_username}.")
            return self.ctx.finish(RunStatus.SUCCESS, "机器人注册成功")
        else:
            interval = self.config.get("interval", 7200)
            self.ctx.next_time = datetime.now() + timedelta(seconds=interval)
            return self.ctx.finish(RunStatus.RESCHEDULE, "机器人未开注")


def use(**kw):
    return type("TemplatedClass", (TemplateCCheckin,), kw)
