import asyncio
import logging

from loguru import logger

from embykeeper.log import formatter
from embykeeper.config import config
from embykeeper.apprise import AppriseStream

debug_logger = logger.bind(scheme="debugtool")
logger = logger.bind(scheme="notifier", nonotify=True)

stream_log = None
stream_msg = None
handler_log_id = None
handler_msg_id = None
change_handle_notifier = None


async def _close_notifier_stream(stream, name):
    try:
        stream.close()
        await stream.join()
    except Exception as e:
        logger.warning(f"{name}消息通知流关闭失败, 已忽略: {type(e).__name__}")


def _notifier_stream_ready(stream) -> bool:
    return bool(getattr(stream, "ready", True))


async def _stop_notifier():
    global stream_log, stream_msg, handler_log_id, handler_msg_id

    if handler_log_id is not None:
        try:
            logger.remove(handler_log_id)
        except ValueError:
            pass
        handler_log_id = None
    if handler_msg_id is not None:
        try:
            logger.remove(handler_msg_id)
        except ValueError:
            pass
        handler_msg_id = None

    if stream_log:
        await _close_notifier_stream(stream_log, "日志")
        stream_log = None
    if stream_msg:
        await _close_notifier_stream(stream_msg, "即时")
        stream_msg = None


def _handle_config_change(*args):
    async def _async():
        global stream_log, stream_msg

        try:
            await _stop_notifier()
            if config.notifier and config.notifier.enabled:
                streams = await start_notifier()
                if streams:
                    stream_log, stream_msg = streams
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"消息通知刷新失败, 已忽略: {type(e).__name__}")

    logger.debug("正在刷新消息通知.")
    asyncio.create_task(_async())


async def start_notifier():
    """消息通知初始化函数."""
    global stream_log, stream_msg, handler_log_id, handler_msg_id, change_handle_notifier

    if (
        stream_log is not None
        or stream_msg is not None
        or handler_log_id is not None
        or handler_msg_id is not None
    ):
        await _stop_notifier()

    def _filter_log(record):
        notify = record.get("extra", {}).get("log", None)
        nonotify = record.get("extra", {}).get("nonotify", None)
        if (not nonotify) and (notify or record["level"].no == logging.ERROR):
            return True
        else:
            return False

    def _filter_msg(record):
        notify = record.get("extra", {}).get("msg", None)
        nonotify = record.get("extra", {}).get("nonotify", None)
        if (not nonotify) and notify:
            return True
        else:
            return False

    def _formatter(record):
        return "{level}#" + formatter(record)

    notifier = config.notifier
    if not notifier or not notifier.enabled:
        if not change_handle_notifier:
            change_handle_notifier = config.on_change("notifier", _handle_config_change)
        return None

    if notifier.method == "apprise":
        if not notifier.apprise_uri:
            logger.error("Apprise URI 未配置, 无法发送消息推送.")
            if not change_handle_notifier:
                change_handle_notifier = config.on_change("notifier", _handle_config_change)
            return None

        logger.info("关键消息将通过 Apprise 推送.")
        next_stream_log = AppriseStream(uri=notifier.apprise_uri)
        next_stream_msg = AppriseStream(uri=notifier.apprise_uri)
        if not _notifier_stream_ready(next_stream_log) or not _notifier_stream_ready(next_stream_msg):
            await _close_notifier_stream(next_stream_log, "日志")
            await _close_notifier_stream(next_stream_msg, "即时")
            if not change_handle_notifier:
                change_handle_notifier = config.on_change("notifier", _handle_config_change)
            return None

        stream_log = next_stream_log
        handler_log_id = logger.add(
            stream_log,
            format=_formatter,
            filter=_filter_log,
            enqueue=True,
        )
        stream_msg = next_stream_msg
        handler_msg_id = logger.add(
            stream_msg,
            format=_formatter,
            filter=_filter_msg,
            enqueue=True,
        )
        if not change_handle_notifier:
            change_handle_notifier = config.on_change("notifier", _handle_config_change)
        return stream_log, stream_msg

    logger.error(f'不支持的消息推送方式 "{notifier.method}", 当前仅支持 apprise.')
    if not change_handle_notifier:
        change_handle_notifier = config.on_change("notifier", _handle_config_change)
    return None


async def debug_notifier():
    streams = await start_notifier()
    if streams:
        logger.info("以下是发送的日志:")
        debug_logger.bind(msg=True).info("这是一条用于测试的即时消息, 使用 debug_notify 触发.")
        debug_logger.bind(log=True).info("这是一条用于测试的日志消息, 使用 debug_notify 触发.")
        if config.notifier.method == "apprise":
            logger.info("已尝试发送, 请至 Apprise 配置的接收端查看.")
        await asyncio.gather(*[stream.join() for stream in streams if stream])
    else:
        logger.error("您当前没有配置有效的日志通知 (未启用日志通知或未配置账号), 请检查配置文件.")
