from .woke import WokeMonitor

__ignore__ = True


class TestWokeMonitor(WokeMonitor):
    name = "Woke 测试"
    chat_name = "api_group"
    chat_allow_outgoing = True
