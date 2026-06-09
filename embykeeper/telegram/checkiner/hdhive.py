from embykeeper.runinfo import RunStatus

from . import BotCheckin

__ignore__ = True


class HDHiveCheckin(BotCheckin):
    name = "HDHive"
    bot_username = "HDHiveBot"
    bot_account_fail_keywords = ["你需要先完成用户初始化"]

    async def send_checkin(self, retry=False):
        await self.send("/checkin")
        await self.finish(RunStatus.SUCCESS, "已发送签到命令")
