import logging
import os
from pathlib import Path
import sys
from functools import wraps
import inspect

import typer
import asyncio
from loguru import logger
from appdirs import user_data_dir

from . import var, __author__, __name__ as __product__, __url__, __version__
from .utils import AsyncTaskPool, show_exception
from .config import config


async def _run_exit_handler(handler):
    try:
        result = handler()
    except Exception as e:
        logger.warning(f"退出处理程序执行失败: {type(e).__name__}.")
        return

    if inspect.isawaitable(result):
        try:
            await result
        except Exception as e:
            logger.warning(f"退出处理程序执行失败: {type(e).__name__}.")
    elif result is not None:
        logger.warning("退出处理程序返回了不可等待对象, 已忽略.")


async def _run_exit_handlers(handlers):
    tasks = [_run_exit_handler(handler) for handler in list(handlers)]
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)


class AsyncTyper(typer.Typer):
    def async_command(self, *args, **kwargs):
        def decorator(async_func):
            @wraps(async_func)
            def sync_func(*_args, **_kwargs):
                async def main():
                    try:
                        await async_func(*_args, **_kwargs)
                    except typer.Exit as e:
                        return e.exit_code
                    except Exception as e:
                        print("\r", end="", flush=True)
                        logger.critical(f"发生关键错误, {__product__.capitalize()} 将退出.")
                        show_exception(e, regular=False)
                        return 1
                    else:
                        logger.info(f"所有任务已完成, 欢迎您再次使用 {__product__.capitalize()}.")

                returncode = 130
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    returncode = loop.run_until_complete(main())
                except KeyboardInterrupt:
                    print("\r正在停止...\r", end="", flush=True, file=sys.stderr)
                finally:
                    if var.exit_handlers:
                        logger.debug("开始执行退出处理程序.")
                        try:
                            # Wait for exit handlers with timeout
                            loop.run_until_complete(_run_exit_handlers(var.exit_handlers))
                        except asyncio.TimeoutError:
                            logger.warning("部分退出处理程序超时未完成.")
                        else:
                            logger.debug("退出处理程序执行完成, 开始清理所有任务.")
                    else:
                        logger.debug("未注册退出处理程序, 开始清理所有任务.")

                    # Then cancel remaining tasks
                    tasks = asyncio.all_tasks(loop)
                    for task in tasks:
                        task.cancel()
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    print("\r", end="", flush=True)
                    logger.info(f"所有服务已停止并登出, 欢迎您再次使用 {__product__.capitalize()}.")
                    raise typer.Exit(returncode)

            self.command(*args, **kwargs)(sync_func)
            return async_func

        return decorator


app = AsyncTyper(
    pretty_exceptions_enable=False,
    rich_markup_mode="rich",
    add_completion=False,
    add_help_option=False,
)


def version(flag):
    if flag:
        print(__version__)
        raise typer.Exit()


def print_example_config(flag):
    if flag:
        print(config.generate_example_config())
        raise typer.Exit()


def print_help(ctx: typer.Context, param: typer.CallbackParam, value: bool):
    if not value or ctx.resilient_parsing:
        return
    typer.echo(ctx.get_help())
    raise typer.Exit()


@app.async_command(
    help=(
        f"欢迎使用 [orange3]{__product__.capitalize()}[/] {__version__} " ":cinema: 无参数默认开启 Emby 保活."
    )
)
async def main(
    config_file: Path = typer.Argument(
        None,
        dir_okay=False,
        allow_dash=True,
        envvar=f"EK_CONFIG_FILE",
        rich_help_panel="参数",
        help="配置文件 (置空以生成)",
    ),
    help: bool = typer.Option(
        None,
        "--help",
        "-h",
        callback=print_help,
        is_eager=True,
        rich_help_panel="调试参数",
        help="显示此帮助信息并退出.",
    ),
    emby: bool = typer.Option(
        False,
        "--emby",
        "-e",
        rich_help_panel="模块开关",
        help="仅启用 Emby 保活功能",
    ),
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        rich_help_panel="调试参数",
        callback=version,
        is_eager=True,
        help=f"打印 {__product__.capitalize()} 版本",
    ),
    example_config: bool = typer.Option(
        None,
        "--example-config",
        "-E",
        hidden=True,
        callback=print_example_config,
        is_eager=True,
        help=f"输出范例配置文件",
    ),
    instant: bool = typer.Option(
        False,
        "--instant/--no-instant",
        "-i/-I",
        envvar="EK_INSTANT",
        show_envvar=False,
        rich_help_panel="调试参数",
        help="启动时立刻执行一次任务",
    ),
    once: bool = typer.Option(
        False,
        "--once/--cron",
        "-o/-O",
        rich_help_panel="调试参数",
        help="只执行一次而不进入计划执行模式",
    ),
    verbosity: int = typer.Option(
        False,
        "--debug",
        "-d",
        count=True,
        envvar="EK_DEBUG",
        show_envvar=False,
        rich_help_panel="调试参数",
        help="开启调试模式",
    ),
    debug_cron: bool = typer.Option(
        False,
        "--debug-cron",
        envvar="EK_DEBUG_CRON",
        show_envvar=False,
        rich_help_panel="调试工具",
        help="开启任务调试模式, 在三秒后立刻开始执行计划任务",
    ),
    debug_notify: bool = typer.Option(
        False,
        "--debug-notify",
        show_envvar=False,
        rich_help_panel="调试工具",
        help="开启日志调试模式, 发送一条日志记录和即时日志记录后退出",
    ),
    simple_log: bool = typer.Option(
        False,
        "--simple-log",
        "-L",
        rich_help_panel="调试参数",
        help="简化日志输出格式",
    ),
    disable_color: bool = typer.Option(
        False,
        "--disable-color",
        "-C",
        rich_help_panel="调试参数",
        help="禁用日志颜色",
    ),
    play: str = typer.Option(
        None,
        "--play-url",
        "-p",
        rich_help_panel="调试工具",
        help="仅模拟观看一个视频",
    ),
    windows: bool = typer.Option(
        False,
        "--windows",
        "-W",
        hidden=True,
        rich_help_panel="调试参数",
        help="启用 Windows 安装部署模式",
    ),
    basedir: Path = typer.Option(
        None,
        "--basedir",
        "-B",
        rich_help_panel="调试参数",
        help="设定账号文件的位置",
    ),
    noexit: bool = typer.Option(
        False,
        "--noexit",
        "-N",
        rich_help_panel="调试参数",
        help="要求所有长期任务在没有账号时继续监控等待",
    ),
    clean: bool = typer.Option(
        False,
        "--clean",
        rich_help_panel="调试工具",
        help="显示或清理 Emby 模拟设备和登陆凭据等缓存",
    ),
):
    from .log import initialize, apply_logging_adapter

    var.debug = verbosity
    if verbosity >= 3:
        level = 0
        logging.getLogger("hpack").setLevel(20)
        asyncio.get_event_loop().set_debug(True)
        apply_logging_adapter(level=10)
    elif verbosity >= 1:
        level = "DEBUG"
    else:
        level = "INFO"

    initialize(level=level, show_path=verbosity and (not simple_log), show_time=not simple_log)
    if disable_color:
        var.console.no_color = True

    msg = " 您可以通过 Ctrl+C 以结束运行."
    logger.info(f"欢迎使用 [orange3]{__product__.capitalize()}[/]! 正在启动, 请稍等.{msg}")
    logger.info(f"当前版本 ({__version__}) 项目页: {__url__}")
    logger.debug(f'命令行参数: "{" ".join(sys.argv[1:])}".')

    basedir = Path(basedir or user_data_dir(__product__))
    basedir.mkdir(parents=True, exist_ok=True)
    logger.info(f'工作目录: "{basedir}", 您的用户数据相关文件将存储在此处, 请妥善保管.')
    docker = bool(os.environ.get("EK_IN_DOCKER", False))
    if docker:
        logger.info("当前在 Docker 容器中运行, 请确认该目录已挂载, 否则文件将在容器重建后丢失.")
    if verbosity:
        logger.warning(f"您当前处于调试模式: 日志等级 {verbosity}.")
        app.pretty_exceptions_enable = True
    config.basedir = basedir
    config.windows = windows

    if not await config.reload_conf(config_file):
        raise typer.Exit(1)

    if verbosity >= 2:
        config.nofail = False
    if not config.nofail:
        logger.warning(f"您当前处于调试模式: 错误将会导致程序停止运行.")
    if debug_cron:
        config.debug_cron = True
        logger.warning("您当前处于计划任务调试模式, 将在 10 秒后运行计划任务.")
    config.noexit = noexit

    if not emby:
        emby = True

    config.on_change(
        "proxy", lambda x, y: logger.bind(scheme="config").warning("修改代理设置后, 可能需要重启程序以生效.")
    )

    if config.mongodb and not var.use_mongodb_config:
        if config.proxy:
            logger.warning("由于不支持, 不使用设定的代理连接 MongoDB 服务器.")
        logger.warning("在本地部署模式下, 不推荐设定使用 MongoDB 缓存.")
        logger.info(f"正在连接到 MongoDB 缓存, 请稍候.")
        try:
            from .cache import cache

            cache.set("test", "test")
            assert cache.get("test", None) == "test"
            cache.delete("test")
        except Exception as e:
            logger.error(f"MongoDB 缓存连接失败: {e}, 程序将退出.")
            show_exception(e, regular=False)
            raise typer.Exit(1)
    else:
        try:
            from .cache import cache

            cache.set("test", "test")
            assert cache.get("test", None) == "test"
            cache.delete("test")
        except Exception as e:
            logger.error(f"本地缓存读写失败: {e}, 请使用 MongoDB 缓存, 程序将退出.")
            show_exception(e, regular=False)
            raise typer.Exit(1)

    if clean:
        from .clean import cleaner

        return await cleaner()

    if play:
        from .emby.main import EmbyManager

        return await EmbyManager().play_url(play)

    if debug_notify:
        from .notify import debug_notifier

        return await debug_notifier()

    try:
        emby_man = None
        if emby:
            from .emby.main import EmbyManager

            emby_man = EmbyManager()

        pool = AsyncTaskPool()
        streams = None

        if instant and not debug_cron:
            if emby_man:
                pool.add(emby_man.run_all(instant=True), "Emby 保活")
            await pool.wait()
            logger.debug("启动时立刻执行 Emby 保活: 已完成.")
        if (not once) or config.noexit:
            from .notify import start_notifier

            streams = await start_notifier()
        if not once:
            if emby_man:
                pool.add(emby_man.schedule_all(), "Emby 保活")
        if config.noexit:
            logger.info("处于长期监控模式, 当没有账号时将继续监控等待新配置.")
            pool.add(asyncio.Event().wait(), "账号配置文件监控")
        try:
            async for t in pool.as_completed():
                try:
                    await t
                except asyncio.CancelledError:
                    logger.debug(f"任务 {t.get_name()} 被取消.")
                except Exception as e:
                    logger.debug(f"任务 {t.get_name()} 出现错误, 模块可能停止运行.")
                    show_exception(e, regular=False)
                    if not config.nofail:
                        raise
                else:
                    logger.debug(f"任务 {t.get_name()} 成功结束.")
        finally:
            if streams:
                await asyncio.gather(*[stream.join() for stream in streams])
    finally:
        from .runinfo import RunContext

        RunContext.cancel_all()


if __name__ == "__main__":
    app()
