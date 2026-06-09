from .bibi import BibiCheckin

__ignore__ = True


class TestBibiCheckin(BibiCheckin):
    ocr = None

    name = "比比 签到测试"
    bot_username = "api_tester_bot"
