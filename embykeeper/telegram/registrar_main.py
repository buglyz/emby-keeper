from __future__ import annotations

import asyncio
import random
import re
import string
from datetime import datetime
from typing import Dict, List

from loguru import logger

from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.schedule import Scheduler
from embykeeper.schema import RegistrarConfig, TelegramAccount
from embykeeper.utils import AsyncTaskPool, format_exception_summary, show_exception

from .dynamic import extract, get_cls
from .embyboss import EmbybossRegister
from .session import ClientsSession

logger = logger.bind(scheme="teleregistrar")


class RegisterManager:
    """定时抢注管理器."""

    def __init__(self, register_callbacks: bool = True):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._schedulers: Dict[str, Scheduler] = {}
        self._scheduler_tasks: Dict[str, asyncio.Task] = {}
        self._pool = AsyncTaskPool()
        self._account_change_handle = None
        self._config_change_handles = []

        if register_callbacks:
            self._account_change_handle = config.on_list_change(
                "telegram.account", self._handle_account_change
            )
            self._config_change_handles = [
                config.on_change("registrar", self._handle_config_change),
                config.on_change("site.registrar", self._handle_config_change),
            ]

    def _telegram_accounts(self) -> List[TelegramAccount]:
        telegram = getattr(config, "telegram", None)
        return list(getattr(telegram, "account", None) or []) if telegram else []

    def _key_for_scheduler(self, scheduler: Scheduler):
        for key, existing in self._schedulers.items():
            if existing is scheduler:
                return key
        return scheduler.sid or str(id(scheduler))

    def _start_scheduler(self, key: str, scheduler: Scheduler):
        if key in self._scheduler_tasks:
            self._scheduler_tasks[key].cancel()
        task = self._pool.add(scheduler.schedule(), f"{key} 抢注计划")
        self._scheduler_tasks[key] = task

        def cleanup(done_task: asyncio.Task):
            if self._scheduler_tasks.get(key) is done_task:
                self._scheduler_tasks.pop(key, None)

        task.add_done_callback(cleanup)
        return task

    async def _wait_pool(self):
        async for task in self._pool.as_completed():
            try:
                await task
            except asyncio.CancelledError:
                logger.debug(f"抢注计划任务 {task.get_name()} 已取消.")
            except Exception as e:
                logger.warning(f"抢注计划任务 {task.get_name()} 异常退出: {format_exception_summary(e)}")
                show_exception(e, regular=False)
                if not config.nofail:
                    raise

    def _handle_config_change(self, *args):
        keys = set(self._tasks) | set(self._schedulers) | set(self._scheduler_tasks)
        for phone in {key.split(".", 1)[0] for key in keys}:
            self.stop_account(phone)

        for account in self._telegram_accounts():
            if account.enabled and account.registrar:
                self._schedule_account_into_pool(account)

        logger.info("已根据新的配置重新安排所有抢注任务.")

    def _handle_account_change(self, added: List[TelegramAccount], removed: List[TelegramAccount]):
        for account in removed:
            self.stop_account(account.phone)
            logger.info(f"{account.phone} 账号的抢注及其计划任务已被清除.")

        for account in added:
            if account.enabled and account.registrar:
                self._schedule_account_into_pool(account)
                logger.info(f"新增的 {account.phone} 账号的抢注计划任务已增加.")

    def _schedule_account_into_pool(self, account: TelegramAccount):
        schedulers, tasks = self.schedule_account(account)
        for scheduler in schedulers:
            self._start_scheduler(self._key_for_scheduler(scheduler), scheduler)
        for task in tasks:
            self._pool.add(task, f"{account.phone} 抢注间隔任务")

    def stop_account(self, phone: str):
        keys = [key for key in self._tasks if key.startswith(f"{phone}.")]
        for key in keys:
            self._tasks[key].cancel()
            del self._tasks[key]

        keys = [key for key in self._schedulers if key.startswith(f"{phone}.")]
        for key in keys:
            del self._schedulers[key]

        keys = [key for key in self._scheduler_tasks if key.startswith(f"{phone}.")]
        for key in keys:
            self._scheduler_tasks[key].cancel()
            del self._scheduler_tasks[key]

    async def shutdown(self):
        """Cancel scheduled/running registrar tasks and unregister config callbacks."""
        tasks = list(self._tasks.values()) + list(self._scheduler_tasks.values()) + list(self._pool.tasks)

        seen = set()
        unique_tasks = []
        for task in tasks:
            if id(task) not in seen:
                seen.add(id(task))
                unique_tasks.append(task)

        for task in unique_tasks:
            if not task.done():
                task.cancel()
        if unique_tasks:
            await asyncio.gather(*unique_tasks, return_exceptions=True)

        self._tasks.clear()
        self._schedulers.clear()
        self._scheduler_tasks.clear()
        self._pool.tasks.clear()

        if self._account_change_handle:
            self._account_change_handle.close()
            self._account_change_handle = None
        for handle in self._config_change_handles:
            handle.close()
        self._config_change_handles = []

    def _sites_for_account(self, account: TelegramAccount) -> List[str]:
        if account.site and account.site.registrar is not None:
            return account.site.registrar
        if config.site and config.site.registrar is not None:
            return config.site.registrar
        return []

    def _registrar_config_for(self, account: TelegramAccount):
        return account.registrar_config or config.registrar or RegistrarConfig()

    def schedule_account(self, account: TelegramAccount):
        """为单个账号安排抢注任务."""
        self.stop_account(account.phone)

        sites = self._sites_for_account(account)
        if not sites:
            phone_masked = TelegramAccount.get_phone_masked(account.phone)
            logger.warning(f"{phone_masked} 账号未配置 registrar 站点, 将跳过抢注调度.")
            return [], []

        clses = extract(get_cls("registrar", names=sites))
        if not clses:
            logger.warning(f"{account.phone} 账号没有有效的 registrar 站点, 将跳过抢注调度.")
            return [], []

        schedulers = []
        tasks = []
        config_to_use = self._registrar_config_for(account)
        for cls in clses:
            site_name = cls.templ_name if hasattr(cls, "templ_name") else cls.__module__.rsplit(".", 1)[-1]
            site_config = config_to_use.get_site_config(site_name)
            if not site_config:
                logger.warning(f"{account.phone} 账号的站点 {site_name} 未配置抢注设置, 将跳过.")
                continue

            if site_config.get("times"):
                schedulers.extend(self._schedule_site_timed(account, site_name, site_config))
            elif site_config.get("interval_minutes"):
                task = self._schedule_site_interval(account, site_name, site_config)
                if task:
                    tasks.append(task)
            else:
                logger.warning(
                    f"{account.phone} 账号的站点 {site_name} 未配置 times 或 interval_minutes, 将跳过."
                )

        return schedulers, tasks

    def _schedule_site_timed(self, account: TelegramAccount, site_name: str, site_config: dict):
        phone_masked = TelegramAccount.get_phone_masked(account.phone)
        times = site_config.get("times", [])
        if isinstance(times, str):
            times = [times]
        interval_days = site_config.get("interval_days", "1")
        schedulers = []

        for idx, run_time in enumerate(times):

            def on_next_time(t: datetime, configured_time=run_time):
                logger.info(
                    f'下一次 "{phone_masked}" 账号 {site_name} 站点 ({configured_time}) 的抢注将在 {t.strftime("%m-%d %H:%M %p")} 进行.'
                )
                date_ctx = RunContext.get_or_create(f"registrar.date.{t.strftime('%Y%m%d')}")
                account_ctx = RunContext.get_or_create(f"registrar.account.{account.phone}")
                site_ctx = RunContext.get_or_create(f"registrar.site.{site_name}")
                return RunContext.prepare(
                    description=f"{account.phone} 账号 {site_name} 站点定时抢注",
                    parent_ids=[account_ctx.id, date_ctx.id, site_ctx.id],
                )

            def func(ctx: RunContext):
                return asyncio.create_task(self._run_single_site(ctx, account, site_name, site_config))

            try:
                scheduler = Scheduler.from_str(
                    func=func,
                    interval_days=interval_days,
                    time_range=run_time,
                    on_next_time=on_next_time,
                    description=f"{account.phone} 账号 {site_name} 站点定时抢注任务 ({run_time})",
                    sid=f"registrar.timed.{account.phone}.{site_name}.{idx}",
                )
            except ValueError as e:
                logger.warning(
                    f"{account.phone} 账号 {site_name} 站点抢注时间 {run_time!r} 无效, 已跳过: {e}"
                )
                continue
            self._schedulers[f"{account.phone}.{site_name}.{idx}"] = scheduler
            schedulers.append(scheduler)

        return schedulers

    def _schedule_site_interval(self, account: TelegramAccount, site_name: str, site_config: dict):
        interval_minutes = site_config.get("interval_minutes")
        if isinstance(interval_minutes, bool):
            logger.warning(f"{account.phone} 账号 {site_name} 站点 interval_minutes 必须为正数, 已跳过.")
            return None
        try:
            interval_minutes = int(interval_minutes)
        except (TypeError, ValueError):
            logger.warning(f"{account.phone} 账号 {site_name} 站点 interval_minutes 无法解析, 已跳过.")
            return None
        if interval_minutes <= 0:
            logger.warning(f"{account.phone} 账号 {site_name} 站点 interval_minutes 必须大于 0, 已跳过.")
            return None
        task = asyncio.create_task(
            self._interval_register_task(account, site_name, site_config, interval_minutes)
        )
        self._tasks[f"{account.phone}.{site_name}"] = task
        return task

    async def _interval_register_task(
        self, account: TelegramAccount, site_name: str, site_config: dict, interval_minutes: int
    ):
        phone_masked = TelegramAccount.get_phone_masked(account.phone)
        while True:
            try:
                account_ctx = RunContext.get_or_create(f"registrar.account.{account.phone}")
                site_ctx = RunContext.get_or_create(f"registrar.site.{site_name}")
                ctx = RunContext.prepare(
                    description=f"{account.phone} 账号 {site_name} 站点间隔抢注",
                    parent_ids=[account_ctx.id, site_ctx.id],
                )
                await self._run_single_site(ctx, account, site_name, site_config)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"{phone_masked} 账号 {site_name} 站点抢注异常: {format_exception_summary(e)}")
                show_exception(e, regular=False)
            await asyncio.sleep(max(1, interval_minutes) * 60)

    async def _run_single_site(
        self, ctx: RunContext, account: TelegramAccount, site_name: str, site_config: dict
    ):
        async with ClientsSession([account]) as clients:
            async for _, client in clients:
                match = re.match(r"templ_a<@?(.+?)>", site_name)
                bot_username = match.group(1) if match else site_name
                log = logger.bind(name=f"{client.me.full_name}, @{bot_username}")

                clses = extract(get_cls("registrar", names=[site_name]))
                if not clses:
                    log.error(f"无法找到站点 {site_name} 的注册器.")
                    return ctx.finish(RunStatus.FAIL, "无法找到注册器")

                cls = clses[0]
                registrar = cls(
                    client,
                    context=ctx,
                    retries=site_config.get("retries", 1),
                    timeout=site_config.get("timeout", 120),
                    config=site_config,
                )
                result = await registrar._start()
                if result.status == RunStatus.SUCCESS:
                    log.bind(log=True).info("抢注成功.")
                elif result.status == RunStatus.IGNORE:
                    log.bind(log=True).info("抢注已跳过.")
                else:
                    log.bind(log=True).warning("抢注失败.")
                return result
        return ctx.finish(RunStatus.FAIL, "无法连接 Telegram 账号")

    async def run_account(self, ctx: RunContext, account: TelegramAccount, instant: bool = False):
        ctx = ctx or RunContext.prepare(f"{account.phone} 账号抢注")
        if ctx.status == RunStatus.PENDING:
            ctx.start(RunStatus.RUNNING)
        config_to_use = self._registrar_config_for(account)
        sites = self._sites_for_account(account)
        if not sites:
            return ctx.finish(RunStatus.NONEED, "未配置抢注站点")

        clses = extract(get_cls("registrar", names=sites))
        if not clses:
            return ctx.finish(RunStatus.NONEED, "没有有效抢注站点")

        sem = asyncio.Semaphore(config_to_use.concurrency or 1)
        tasks = []
        for cls in clses:
            site_name = cls.templ_name if hasattr(cls, "templ_name") else cls.__module__.rsplit(".", 1)[-1]
            site_config = config_to_use.get_site_config(site_name)
            if site_config:
                tasks.append(self._run_with_sem(sem, ctx, account, site_name, site_config))

        if not tasks:
            return ctx.finish(RunStatus.NONEED, "未配置可执行抢注任务")

        task_handles = [asyncio.create_task(task) for task in tasks]
        try:
            results = await asyncio.gather(*task_handles)
        except asyncio.CancelledError:
            for task in task_handles:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*task_handles, return_exceptions=True)
            if not ctx._finished.is_set():
                ctx.finish(RunStatus.CANCELLED, "任务被取消")
            raise
        except Exception as e:
            for task in task_handles:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*task_handles, return_exceptions=True)
            if not ctx._finished.is_set():
                ctx.finish(RunStatus.ERROR, "抢注异常")
            logger.warning(f"{account.phone} 账号抢注异常: {format_exception_summary(e)}")
            show_exception(e, regular=False)
            raise
        statuses = [result.status for result in results if isinstance(result, RunContext)]
        if any(status == RunStatus.SUCCESS for status in statuses):
            return ctx.finish(RunStatus.SUCCESS, "抢注任务已完成")
        if any(status in {RunStatus.FAIL, RunStatus.ERROR} for status in statuses):
            return ctx.finish(RunStatus.FAIL, "抢注失败")
        return ctx.finish(RunStatus.NONEED, "抢注任务未执行或已跳过")

    async def _run_with_sem(self, sem, ctx, account, site_name, site_config):
        async with sem:
            site_ctx = RunContext.prepare(f"{site_name} 站点抢注", parent_ids=ctx.id)
            return await self._run_single_site(site_ctx, account, site_name, site_config)

    async def run_all(self, instant: bool = False):
        accounts = [a for a in self._telegram_accounts() if a.enabled and a.registrar]
        tasks = [
            asyncio.create_task(self.run_account(RunContext.prepare("运行全部抢注器"), account, instant))
            for account in accounts
        ]
        try:
            if tasks:
                await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def schedule_all(self):
        all_schedulers = []
        all_tasks = []
        for account in self._telegram_accounts():
            if account.enabled and account.registrar:
                schedulers, tasks = self.schedule_account(account)
                all_schedulers.extend(schedulers)
                all_tasks.extend(tasks)
        return all_schedulers, all_tasks

    async def start(self):
        schedulers, tasks = await self.schedule_all()
        if not schedulers and not tasks:
            logger.info("没有需要执行的 Telegram 机器人抢注任务")
            return

        logger.info(f"已创建 {len(schedulers)} 个定时抢注调度器和 {len(tasks)} 个间隔抢注任务.")
        for scheduler in schedulers:
            self._start_scheduler(self._key_for_scheduler(scheduler), scheduler)
        for task in tasks:
            self._pool.add(task, "抢注间隔任务")
        await self._wait_pool()

    async def run_single_bot(
        self,
        bot_username: str,
        instant: bool = True,
        accounts: List[TelegramAccount] = None,
        username: str = None,
        password: str = None,
        interval_seconds: int = 1,
        ctx: RunContext = None,
    ):
        accounts = accounts if accounts is not None else [a for a in self._telegram_accounts() if a.enabled]
        if not accounts:
            logger.error("没有可用的 Telegram 账号.")
            return []
        tasks = [
            asyncio.create_task(
                self._run_single_bot_for_account(
                    account,
                    bot_username,
                    username=username,
                    password=password,
                    interval_seconds=interval_seconds,
                    ctx=ctx,
                )
            )
            for account in accounts
        ]
        try:
            return await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _run_single_bot_for_account(
        self,
        account: TelegramAccount,
        bot_username: str,
        username: str = None,
        password: str = None,
        interval_seconds: int = 1,
        ctx: RunContext = None,
    ):
        async with ClientsSession([account]) as clients:
            async for _, client in clients:
                log = logger.bind(name=f"{client.me.full_name}, @{bot_username}")
                if ctx:
                    log = ctx.bind_logger(log)
                register = EmbybossRegister(
                    client=client,
                    logger=log,
                    username=username or client.me.username or f"user_{client.me.id}",
                    password=password or "".join(random.choices(string.ascii_letters + string.digits, k=4)),
                )
                return await register.run_continuous(bot_username, interval_seconds)
        return False
