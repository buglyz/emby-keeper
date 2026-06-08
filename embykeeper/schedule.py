import asyncio
import random
from datetime import datetime, time, timedelta
from dateutil import parser
import re
from typing import Callable, Union
import json
import hashlib

from loguru import logger

from .config import config
from .runinfo import RunContext, RunStatus
from .utils import looks_like_time_text, next_random_datetime


class Scheduler:
    """异步函数计划执行器"""

    @classmethod
    def from_str(
        cls,
        func: Callable,
        interval_days: str,
        time_range: str,
        **kw,
    ):
        """从字符串创建调度器

        Args:
            func: 要执行的异步函数
            interval_days: 间隔天数字符串, 支持数字或 "<min,max>" 格式
            time_range: 时间范围字符串, 支持具体时间或 "<start,end>" 格式
        Returns:
            Scheduler: 调度器实例
        """
        interval_days = str(interval_days).strip()
        time_range = str(time_range).strip()

        # Parse interval days
        interval_range_match = re.fullmatch(r"<\s*(\d+)\s*,\s*(\d+)\s*>", interval_days)
        if interval_range_match:
            days = [int(interval_range_match.group(1)), int(interval_range_match.group(2))]
            if days[0] <= 0 or days[1] <= 0:
                raise ValueError(f"间隔天数必须大于 0: {interval_days}")
            if days[0] > days[1]:
                raise ValueError(f"间隔天数范围无效: {interval_days}")
        else:
            try:
                days = int(interval_days)
            except ValueError:
                raise ValueError(f"无法解析间隔天数: {interval_days}")
            if days <= 0:
                raise ValueError(f"间隔天数必须大于 0: {interval_days}")

        # Parse time range
        time_range_match = re.fullmatch(r"<\s*(.*?)\s*,\s*(.*?)\s*>", time_range)
        if time_range_match:
            start_time, end_time = time_range_match.group(1), time_range_match.group(2)
        else:
            start_time = end_time = time_range

        return cls(
            func,
            days=days,
            start_time=start_time,
            end_time=end_time,
            **kw,
        )

    @staticmethod
    def _validate_days(days):
        if isinstance(days, (list, tuple)):
            if len(days) != 2:
                raise ValueError("执行间隔天数范围必须包含两个值")
            normalized = []
            for day in days:
                if not isinstance(day, int) or isinstance(day, bool):
                    raise ValueError("执行间隔天数必须为整数")
                if day < 0:
                    raise ValueError("执行间隔天数不能小于 0")
                normalized.append(day)
            if normalized[0] > normalized[1]:
                raise ValueError("执行间隔天数范围无效")
            return normalized
        if not isinstance(days, int) or isinstance(days, bool):
            raise ValueError("执行间隔天数必须为整数")
        if days < 0:
            raise ValueError("执行间隔天数不能小于 0")
        return days

    def __init__(
        self,
        func: Callable,
        days: Union[int, list] = 1,
        start_time: Union[str, time] = None,
        end_time: Union[str, time] = None,
        sid: str = None,
        description: str = None,
        on_next_time: Callable[[datetime], None] = None,
    ):
        """
        Args:
            func: 要执行的异步函数
            days: 执行间隔天数, 可以是固定天数或者[最小天数, 最大天数]
            start_time: 执行时间范围起始时间 (可选)
            end_time: 执行时间范围结束时间 (可选)
            sid: 调度器ID, 用于缓存下次执行时间
            description: 调度器描述
            on_next_time: 回调函数, 在计算出下一次执行时间时调用
        """
        self.func = func
        if config.debug_cron:
            logger.warning(f"计划任务调试模式下任务开始时间被调整为10秒后: {description}")
            debug_time = (datetime.now() + timedelta(seconds=10)).time()
            self.days = 0
            self.start_time = debug_time
            self.end_time = debug_time
        else:
            self.days = self._validate_days(days)
            self.start_time = self._parse_time(start_time)
            self.end_time = self._parse_time(end_time)
        self.sid = sid
        self.description = description
        self.on_next_time = on_next_time
        self._cache_key = f"scheduler.{sid}" if sid else None
        self._next_time = None
        self._ctx: RunContext = None

    def _parse_time(self, t):
        if isinstance(t, str):
            if not looks_like_time_text(t):
                raise ValueError(f"无法解析时间: {t}")
            return parser.parse(t).time()
        if t is not None and not isinstance(t, time):
            raise ValueError("执行时间必须为字符串或 datetime.time")
        return t

    def _get_scheduler_config(self):
        """获取调度器配置的哈希值"""
        config = {
            "days": self.days,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }
        # Convert config to a stable string representation and hash it
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()

    def _runs_once(self) -> bool:
        return self.days == 0 or (
            isinstance(self.days, (list, tuple)) and self.days[0] == 0 and self.days[1] == 0
        )

    @property
    def next_time(self) -> datetime:
        """获取下一次执行时间"""
        if not self._next_time:
            self._next_time = self._get_next_time()
        return self._next_time

    def _get_next_time(self) -> datetime:
        """计算或获取缓存的下一次执行时间"""
        from .cache import cache

        now = datetime.now()
        next_time = None

        # Try to get cached next execution time
        if self._cache_key:
            try:
                cached = cache.get(self._cache_key)
            except Exception as e:
                logger.warning(f"计划任务缓存读取失败, 已忽略: {type(e).__name__}")
                cached = None
            if isinstance(cached, dict):
                cached_config_hash = cached.get("config_hash")
                cached_time = cached.get("next_time")
                try:
                    cached_next_time = parser.parse(cached_time) if cached_time else None
                except (TypeError, ValueError, parser.ParserError):
                    cached_next_time = None

                # Check if config hash matches and time hasn't passed
                try:
                    use_cached_time = (
                        cached_config_hash == self._get_scheduler_config()
                        and cached_next_time
                        and cached_next_time > now
                    )
                except TypeError:
                    use_cached_time = False

                if use_cached_time:
                    next_time = cached_next_time

        # Calculate new next_time if needed
        if not next_time:
            # Calculate interval days
            if isinstance(self.days, (list, tuple)):
                interval = random.randint(self.days[0], self.days[1])
            else:
                interval = self.days

            next_time = next_random_datetime(
                start_time=self.start_time, end_time=self.end_time, interval_days=interval
            )

            # Cache the next execution time with config hash
            if self._cache_key:
                try:
                    cache.set(
                        self._cache_key,
                        {
                            "config_hash": self._get_scheduler_config(),
                            "next_time": next_time.isoformat(),
                            "description": self.description,
                        },
                    )
                except Exception as e:
                    logger.warning(f"计划任务缓存写入失败, 已忽略: {type(e).__name__}")

        return next_time

    async def schedule(self):
        """等待到指定时间范围内执行函数"""
        from .cache import cache

        while True:
            now = datetime.now()
            self._next_time = self._get_next_time()

            # Call the hook function if provided
            if self.on_next_time:
                try:
                    self._ctx = self.on_next_time(self._next_time)
                except Exception as e:
                    logger.warning(f"计划任务时间回调执行失败, 已忽略: {type(e).__name__}")
                    self._ctx = None

            # Wait until the scheduled time
            wait_seconds = (self._next_time - now).total_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)

            # Execute the function
            try:
                task = asyncio.create_task(self.func(self._ctx))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if task.done():
                        if self._ctx:
                            self._ctx.finish(RunStatus.ERROR, "任务在运行时被取消")
                    else:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.warning(f"计划任务取消时子任务退出异常, 已忽略: {type(e).__name__}")
                        if self._ctx:
                            self._ctx.finish(RunStatus.CANCELLED, "任务被取消")
                    raise
            except asyncio.CancelledError:
                # This is a cancellation from outside schedule()
                if self._ctx and self._ctx.status not in {RunStatus.CANCELLED, RunStatus.ERROR}:
                    self._ctx.finish(RunStatus.CANCELLED, "任务被取消")
                raise  # Re-raise to propagate cancellation
            except Exception:
                if self._ctx:
                    self._ctx.finish(RunStatus.ERROR, f"任务发生错误")
                if not config.nofail:
                    raise

            if self._cache_key:
                try:
                    cache.delete(self._cache_key)
                except Exception as e:
                    logger.warning(f"计划任务缓存删除失败, 已忽略: {type(e).__name__}")
                    pass
            self._ctx = None
            self._next_time = None

            # If days is 0, break the loop after one execution
            if self._runs_once():
                break
