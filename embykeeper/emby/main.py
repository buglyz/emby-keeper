import asyncio
import random
from typing import List, Dict, Set, Optional
from urllib.parse import parse_qs, urlparse
from datetime import datetime

from loguru import logger

from embykeeper.config import config
from embykeeper.schedule import Scheduler
from embykeeper.utils import show_exception, truncate_str
from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.var import console
from embykeeper.schema import EmbyAccount, EmbyConfig

from .api import Emby, EmbyPlayError, EmbyConnectError, EmbyRequestError, EmbyError

logger = logger.bind(scheme="embywatcher")
_INVALID_PORT = object()


def _get_emby_config() -> EmbyConfig:
    if config._cache and config._cache.emby:
        return config._cache.emby
    return EmbyConfig()


def _default_url_port(scheme: str) -> Optional[int]:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _parsed_url_port(parsed_url):
    try:
        return parsed_url.port
    except ValueError:
        return _INVALID_PORT


def _same_emby_origin(account: EmbyAccount, parsed_url) -> bool:
    if account.url.scheme != parsed_url.scheme:
        return False
    if account.url.host != parsed_url.hostname:
        return False
    account_port = account.url.port or _default_url_port(account.url.scheme)
    parsed_port = _parsed_url_port(parsed_url)
    if parsed_port is _INVALID_PORT:
        return False
    parsed_port = parsed_port or _default_url_port(parsed_url.scheme)
    return account_port == parsed_port


def _extract_emby_item_id(parsed_url) -> Optional[str]:
    query_groups = [parse_qs(parsed_url.query)]
    fragment = parsed_url.fragment
    if "?" in fragment:
        query_groups.append(parse_qs(fragment.split("?", 1)[1]))
    elif fragment.startswith("?"):
        query_groups.append(parse_qs(fragment[1:]))

    for params in query_groups:
        for key in ("id", "itemId", "ItemId"):
            values = params.get(key)
            if values and values[0]:
                item_id = values[0].strip()
                if item_id:
                    return item_id
    return None


class EmbyManager:
    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}  # account_spec -> task
        self._scheduler_tasks: Dict[str, asyncio.Task] = {}  # account_spec -> scheduler task
        self._schedulers: Dict[str, Scheduler] = {}  # account_spec -> scheduler
        self._running: Set[str] = set()  # Currently running account_specs

        self._account_change_handle = config.on_list_change("emby.account", self._handle_account_change)
        self._schedule_change_handles = [
            config.on_change("emby.time_range", self._handle_schedule_config_change),
            config.on_change("emby.interval_days", self._handle_schedule_config_change),
        ]

    def _reschedule_accounts(self):
        """Rebuild all scheduler tasks from the current Emby account config."""
        account_specs = list(self._schedulers.keys())
        for account_spec in account_specs:
            if account_spec == "unified":
                self.stop_unified_accounts()
            else:
                self.stop_account(account_spec)

        self.schedule_unified_accounts()
        for account in _get_emby_config().account or []:
            if account.enabled and (account.time_range or account.interval_days):
                scheduler = self.schedule_independent_account(account)
                if scheduler:
                    self._start_scheduler(self.get_spec(account), scheduler)

    def _handle_schedule_config_change(self, old, new):
        """Handle global Emby schedule changes without requiring a restart."""
        if old == new:
            return
        self._reschedule_accounts()
        logger.info("Emby 保活全局计划设置已更新, 已重新调度保活任务.")

    def _start_scheduler(self, account_spec: str, scheduler: Scheduler):
        if account_spec in self._scheduler_tasks:
            self._scheduler_tasks[account_spec].cancel()
        task = asyncio.create_task(scheduler.schedule(), name=account_spec)
        self._scheduler_tasks[account_spec] = task

        def cleanup(done_task: asyncio.Task):
            if self._scheduler_tasks.get(account_spec) is done_task:
                self._scheduler_tasks.pop(account_spec, None)

        task.add_done_callback(cleanup)

    def _start_watch_task(self, account_spec: str, coro):
        task = asyncio.create_task(coro, name=f"watch-{account_spec}")
        self._tasks[account_spec] = task

        def cleanup(done_task: asyncio.Task):
            if self._tasks.get(account_spec) is done_task:
                self._tasks.pop(account_spec, None)

        task.add_done_callback(cleanup)
        return task

    def _handle_account_change(self, added: List[EmbyAccount], removed: List[EmbyAccount]):
        """Handle account additions and removals"""
        need_reschedule_unified = False

        for account in removed:
            spec = self.get_spec(account)
            if account.time_range or account.interval_days:
                # 独立账号, 直接移除其任务
                self.stop_account(spec)
                logger.info(f"账号 {spec} 的 Emby 保活及其计划任务已被清除.")
            else:
                # 整体账号被移除, 标记需要重新调度
                need_reschedule_unified = True
                logger.info(f"账号 {spec} Emby 保活已被移除, 将重新调度保活任务.")

        for account in added:
            if account.enabled:
                if account.time_range or account.interval_days:
                    # 新增独立账号, 添加其调度任务
                    scheduler = self.schedule_independent_account(account)
                    if scheduler:
                        self._start_scheduler(self.get_spec(account), scheduler)
                        logger.info(f"新增的账号 {self.get_spec(account)} 的 Emby 保活计划任务已添加.")
                else:
                    # 新增整体账号, 标记需要重新调度
                    need_reschedule_unified = True
                    logger.debug(f"新增的账号 {self.get_spec(account)}, 将重新调度 Emby 保活任务.")

        if need_reschedule_unified:
            # 重新调度整体任务
            self.stop_unified_accounts()
            self.schedule_unified_accounts()

    def stop_account(self, account_spec: str):
        """Stop scheduling and running tasks for an independent account"""
        if account_spec in self._schedulers:
            del self._schedulers[account_spec]

        if account_spec in self._scheduler_tasks:
            self._scheduler_tasks[account_spec].cancel()
            del self._scheduler_tasks[account_spec]

        if account_spec in self._tasks:
            self._tasks[account_spec].cancel()
            del self._tasks[account_spec]

        self._running.discard(account_spec)

    def stop_unified_accounts(self):
        """Stop the unified scheduling task"""
        if "unified" in self._schedulers:
            del self._schedulers["unified"]

        if "unified" in self._scheduler_tasks:
            self._scheduler_tasks["unified"].cancel()
            del self._scheduler_tasks["unified"]

        if "unified" in self._tasks:
            self._tasks["unified"].cancel()
            del self._tasks["unified"]

    def schedule_independent_account(self, account: EmbyAccount) -> Optional[Scheduler]:
        """Schedule emby watch for an independent account"""
        if not account.enabled:
            return None

        account_spec = self.get_spec(account)
        emby_config = _get_emby_config()
        time_range = account.time_range or emby_config.time_range
        interval = account.interval_days or emby_config.interval_days

        def make_on_next_time(spec):
            return lambda t: logger.bind(log=True).info(
                f"下一次 Emby 账号 ({spec}) 的保活将在 {t.strftime('%m-%d %H:%M %p')} 进行."
            )

        def func(ctx: RunContext):
            return self._start_watch_task(self.get_spec(account), self._watch_main([account], False))

        scheduler = Scheduler.from_str(
            func=func,
            interval_days=interval,
            time_range=time_range,
            on_next_time=make_on_next_time(account_spec),
            sid=f"emby.watch.{account_spec}",
            description=f"Emby 保活任务 - {account_spec}",
        )
        self._schedulers[account_spec] = scheduler
        return scheduler

    def schedule_unified_accounts(self):
        """Schedule unified emby watch for global accounts"""
        emby_config = _get_emby_config()
        unified_accounts = [
            a for a in emby_config.account or [] if a.enabled and not (a.time_range or a.interval_days)
        ]

        if not unified_accounts:
            return None

        on_next_time = lambda t: logger.bind(log=True).info(
            f"下一次 Emby 保活将在 {t.strftime('%m-%d %H:%M %p')} 进行."
        )

        def func(ctx: RunContext):
            return self._start_watch_task("unified", self._watch_main(unified_accounts, False))

        scheduler = Scheduler.from_str(
            func=func,
            interval_days=emby_config.interval_days,
            time_range=emby_config.time_range,
            on_next_time=on_next_time,
            sid="emby.watch.global",
            description="Emby 保活任务",
        )
        self._schedulers["unified"] = scheduler
        self._start_scheduler("unified", scheduler)

    async def schedule_all(self, instant: bool = False):
        """Start scheduling emby watch for all accounts"""
        # Schedule unified accounts
        self.schedule_unified_accounts()

        # Schedule independent accounts
        for account in _get_emby_config().account or []:
            if account.enabled and (account.time_range or account.interval_days):
                scheduler = self.schedule_independent_account(account)
                if scheduler:
                    self._start_scheduler(self.get_spec(account), scheduler)

        if not self._schedulers:
            logger.info("没有需要执行的 Emby 保活任务")
            return None

        while self._scheduler_tasks:
            done, _ = await asyncio.wait(
                list(self._scheduler_tasks.values()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if task.cancelled():
                    continue
                try:
                    task.result()
                except Exception as e:
                    logger.warning("Emby 保活计划任务异常退出.")
                    show_exception(e, regular=False)
                    if not config.nofail:
                        raise

    async def shutdown(self):
        """Cancel scheduled and running Emby watch tasks cleanly."""
        tasks = list(self._scheduler_tasks.values()) + list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._scheduler_tasks.clear()
        self._tasks.clear()
        self._schedulers.clear()
        self._running.clear()
        if self._account_change_handle:
            self._account_change_handle.__exit__(None, None, None)
            self._account_change_handle = None
        for handle in self._schedule_change_handles:
            handle.__exit__(None, None, None)
        self._schedule_change_handles = []

    async def play_url(self, url: str):
        parsed = urlparse(url)
        iid = _extract_emby_item_id(parsed)
        if not iid:
            logger.error(
                "无效的 URL 格式, 无法解析视频 ID. 应为类似:\nhttps://example.com/web/#/details?id=xxx&serverId=xxx"
            )
            return False

        # 在config中查找匹配的emby配置
        account = None
        for a in _get_emby_config().account or []:
            if _same_emby_origin(a, parsed):
                account = a
                break

        if not account:
            logger.error(f"在配置中未找到匹配的 Emby 服务器: {parsed.netloc}")
            return False

        ctx = RunContext.prepare(description="播放指定 URL 视频")
        ctx.start(RunStatus.INITIALIZING)

        emby = Emby(account)
        try:
            if not await emby.ensure_authenticated():
                return ctx.finish(RunStatus.FAIL, "登陆失败")
            emby.log.info("使用以下 Headers:")
            console.rule("Headers")
            headers = emby.build_headers()
            for k, v in headers.items():
                console.print(f"{k.title()}: {v}")
            console.rule()
            item = await emby.get_item(iid)
            if not item:
                raise ValueError(f"无法找到 ID 为 {iid} 的视频")
            name = truncate_str(item.get("Name", "(未命名视频)"), 10)
            emby.log.info(f'10 秒后, 将开始播放该视频 300 秒: "{name}"')
            await asyncio.sleep(1)
            emby.log.info(f'开始播放视频 300 秒: "{name}"')
            try:
                await emby.play(item, time=300)
            except EmbyPlayError as e:
                emby.log.error(f"播放失败: {e}")
                return ctx.finish(RunStatus.FAIL, "播放失败")
            return ctx.finish(RunStatus.SUCCESS, "播放成功")
        except EmbyConnectError as e:
            if emby.proxy:
                emby.log.error(f"无法连接到服务器, 可能是您的代理服务器设置错误或无法连通: {e}")
            else:
                emby.log.error(f"无法连接到服务器, 可能是您没有使用代理: {e}")
            return ctx.finish(RunStatus.FAIL, "连接失败")
        except EmbyRequestError as e:
            emby.log.error(f"服务器异常: {e}")
            return ctx.finish(RunStatus.FAIL, "服务器异常")
        except Exception as e:
            emby.log.error("播放视频时发生错误, 播放失败.")
            show_exception(e, regular=False)
            return ctx.finish(RunStatus.ERROR, "异常错误")

    def get_spec(self, a: EmbyAccount):
        return f"{a.username}@{a.name or a.url.host}"

    async def _watch_main(self, accounts: List[EmbyAccount], instant: bool = False):
        enabled_accounts = [account for account in accounts if account.enabled]
        if not enabled_accounts:
            return None
        logger.info("开始执行 Emby 保活.")
        tasks = []
        sem = asyncio.Semaphore(_get_emby_config().concurrency or 100000)

        ctx = RunContext.prepare(description="使用全局设置的 Emby 统一保活")
        ctx.start(RunStatus.INITIALIZING)

        async def watch_wrapper(account: EmbyAccount, sem):
            async with sem:
                spec = self.get_spec(account)
                self._running.add(spec)
                try:
                    try:
                        emby = Emby(account)
                    except Exception as e:
                        logger.error(f"初始化失败: {e}")
                        show_exception(e, regular=False)
                        return account, False
                    if not instant:
                        wait = random.uniform(180, 360)
                        emby.log.info(f"播放视频前随机等待 {wait:.0f} 秒.")
                        await asyncio.sleep(wait)
                    if not account.play_id:
                        emby.log.info(f"正在登陆并获取首页视频项目.")
                        if not await emby.ensure_authenticated():
                            emby.log.warning(f"保活失败: 无法登陆.")
                            return account, False
                        await emby.load_main_page()
                        if not emby.items:
                            emby.log.warning("保活失败: 无法获取首页中的视频项目")
                            return account, False
                        else:
                            emby.log.info(f"成功登陆, 获取了 {len(emby.items)} 个首页视频项目.")
                        await asyncio.sleep(random.uniform(2, 5))
                    else:
                        emby.log.info(f"正在登陆并播放您指定的视频, ID 为 {account.play_id}.")
                        if not await emby.ensure_authenticated():
                            emby.log.warning(f"保活失败: 无法登陆.")
                            return account, False
                        item = await emby.get_item(account.play_id)
                        if not "Id" in item:
                            emby.log.warning("保活失败: 无法获取视频项目")
                            return account, False
                        else:
                            emby.items[item["Id"]] = item
                            emby.log.info(f"成功登陆, 获取了视频项目.")
                        await asyncio.sleep(random.uniform(2, 5))
                    return account, await emby.watch()
                except EmbyError as e:
                    emby.log.warning(f"保活失败: {e}.")
                    return account, False
                except Exception as e:
                    emby.log.warning(f"保活失败: {e}")
                    show_exception(e, regular=False)
                    return account, False
                finally:
                    self._running.discard(spec)

        for account in enabled_accounts:
            tasks.append(watch_wrapper(account, sem))

        failed_accounts = []
        successful_accounts = []
        try:
            results = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            ctx.finish(RunStatus.CANCELLED, "任务被取消")
            raise
        for a, success in results:
            if success:
                successful_accounts.append(self.get_spec(a))
            else:
                failed_accounts.append(self.get_spec(a))
        fails = len(failed_accounts)

        if fails:
            if len(enabled_accounts) == 1:
                logger.error(f"保活失败: {', '.join(failed_accounts)}")
            else:
                logger.error(f"保活失败 ({fails}/{len(tasks)}): {', '.join(failed_accounts)}")
            return ctx.finish(RunStatus.FAIL, f"保活失败")
        if len(enabled_accounts) == 1:
            logger.bind(log=True).info(f"保活成功: {', '.join(successful_accounts)}.")
        else:
            logger.bind(log=True).info(
                f"保活成功 ({len(tasks)}/{len(tasks)}): {', '.join(successful_accounts)}."
            )
        return ctx.finish(RunStatus.SUCCESS, f"保活成功")

    async def run_all(self, instant: bool = False):
        return await self._watch_main(_get_emby_config().account or [], instant)
