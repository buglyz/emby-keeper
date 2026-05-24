from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..models import GlobalConfigResponse, GlobalConfigUpdate
from embykeeper.config import config

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=GlobalConfigResponse)
async def get_config(user: str = Depends(get_current_user)):
    """Read current global config (sensitive fields masked)."""
    if not config._cache:
        return GlobalConfigResponse()

    emby = config._cache.emby
    proxy = config._cache.proxy

    return GlobalConfigResponse(
        emby_time_range=emby.time_range,
        emby_interval_days=emby.interval_days,
        emby_concurrency=emby.concurrency,
        proxy_hostname=proxy.hostname if proxy else None,
        proxy_port=proxy.port if proxy else None,
        proxy_scheme=proxy.scheme if proxy else None,
    )


@router.put("")
async def update_config(req: GlobalConfigUpdate, user: str = Depends(get_current_user)):
    """Update global config settings."""
    if not config._cache:
        return {"error": "Config not loaded"}

    new_config = config._cache.model_copy()

    # Update Emby config
    if req.emby_time_range:
        new_config.emby.time_range = req.emby_time_range
    if req.emby_interval_days:
        new_config.emby.interval_days = req.emby_interval_days
    if req.emby_concurrency:
        new_config.emby.concurrency = req.emby_concurrency

    # Update proxy config
    if req.proxy:
        from embykeeper.schema import ProxyConfig
        existing_proxy = new_config.proxy or ProxyConfig()
        if req.proxy.hostname:
            existing_proxy.hostname = req.proxy.hostname
        if req.proxy.port:
            existing_proxy.port = req.proxy.port
        if req.proxy.scheme:
            existing_proxy.scheme = req.proxy.scheme
        new_config.proxy = existing_proxy

    config.set(new_config)

    return {"status": "updated"}