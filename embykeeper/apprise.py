import apprise
from datetime import datetime
from loguru import logger
from rich.text import Text

logger = logger.bind(scheme="notifier", nonotify=True)
_last_delivery_status = {
    "status": None,
    "time": None,
    "error": None,
}


def _record_delivery_status(status: str, error: str = None):
    _last_delivery_status.update(
        {
            "status": status,
            "time": datetime.now(),
            "error": error,
        }
    )


def get_delivery_status():
    return dict(_last_delivery_status)


class AppriseStream:
    def __init__(self, uri: str):
        self.apobj = apprise.Apprise()
        self.ready = False
        try:
            self.ready = bool(self.apobj.add(uri))
        except Exception as e:
            _record_delivery_status("error", type(e).__name__)
            logger.warning(f"Failed to configure Apprise notification URI: {type(e).__name__}.")
            return
        if not self.ready:
            _record_delivery_status("error", "InvalidURI")
            logger.warning("Failed to configure Apprise notification URI.")

    def write(self, message):
        if not self.ready:
            return
        # The message from loguru has a newline at the end, remove it.
        message = message.strip()
        if not message:
            return
        # The message is formatted as "LEVEL#MESSAGE"
        level, separator, body = message.partition("#")
        if not separator:
            level = "info"
            body = message
        level = level.lower()
        try:
            body = Text.from_markup(body).plain
        except Exception:
            pass

        # Map loguru levels to apprise levels
        notify_type = apprise.NotifyType.INFO
        if level == "warning":
            notify_type = apprise.NotifyType.WARNING
        elif level == "error" or level == "critical":
            notify_type = apprise.NotifyType.FAILURE
        elif level == "success":
            notify_type = apprise.NotifyType.SUCCESS

        try:
            sent = self.apobj.notify(body=body, title="Embykeeper", notify_type=notify_type)
        except Exception as e:
            _record_delivery_status("error", type(e).__name__)
            logger.warning(f"Failed to send notification via Apprise: {type(e).__name__}.")
            return
        if not sent:
            _record_delivery_status("error", "SendFailed")
            logger.warning("Failed to send notification via Apprise.")
            return
        _record_delivery_status("sent")

    def close(self):
        pass

    async def join(self):
        pass
