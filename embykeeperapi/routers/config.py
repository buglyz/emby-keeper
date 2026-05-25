from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user
from ..models import GlobalConfigResponse, GlobalConfigUpdate
from embykeeper.config import config

router = APIRouter(prefix="/api/config", tags=["config"])


def _model_fields_set(model) -> set:
    return getattr(model, "model_fields_set", getattr(model, "__fields_set__", set()))


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
        raise HTTPException(status_code=503, detail="Config not loaded")

    new_config = config._cache.model_copy()

    fields_set = _model_fields_set(req)

    if "emby_time_range" in fields_set:
        new_config.emby.time_range = req.emby_time_range
    if "emby_interval_days" in fields_set:
        new_config.emby.interval_days = req.emby_interval_days
    if "emby_concurrency" in fields_set:
        new_config.emby.concurrency = req.emby_concurrency

    if "proxy" in fields_set and req.proxy is not None:
        from embykeeper.schema import ProxyConfig

        existing_proxy = new_config.proxy or ProxyConfig()
        proxy_fields_set = _model_fields_set(req.proxy)
        if "hostname" in proxy_fields_set:
            existing_proxy.hostname = req.proxy.hostname
        if "port" in proxy_fields_set:
            existing_proxy.port = req.proxy.port
        if "scheme" in proxy_fields_set:
            existing_proxy.scheme = req.proxy.scheme
        new_config.proxy = existing_proxy

    config.set(new_config)

    return {"status": "updated"}