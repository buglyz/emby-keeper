from loguru import logger


logger = logger.bind(scheme="cfsolver")


async def get_cf_clearance(url: str, proxy: str = None):
    logger.warning("Cloudflare 自动验证解析已禁用。请在浏览器中完成验证后再重试，或选择未启用 Cloudflare 验证的 Emby 入口。")
    return None, None
