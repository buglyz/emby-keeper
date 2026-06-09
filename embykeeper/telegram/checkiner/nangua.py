from ._templ_a import TemplateACheckin


class NanguaCheckin(TemplateACheckin):
    name = "南瓜音乐"
    bot_username = "nanguaemby_bot"
    bot_success_pat = "签到成功"
    bot_account_fail_keywords = ["你有号吗你就签到"]
