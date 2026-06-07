from pathlib import Path
from collections.abc import MutableMapping
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from tomlkit import document, dumps, parse

from ..auth import get_current_user
from ..models import GlobalConfigResponse, GlobalConfigUpdate
from ..validation import validate_schedule_fields
from embykeeper.config import config
from embykeeper.schema import ProxyConfig

router = APIRouter(prefix="/api/config", tags=["config"])


def _model_fields_set(model) -> set:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is not None:
        return fields_set
    return getattr(model, "__fields_set__", set())


def _write_text_atomic(path: Path, content: str):
    tmp_path = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _persist_global_config(next_config=None):
    """Persist Web UI global settings without rewriting account secrets."""
    target_config = next_config or config._cache
    config_file = Path(config.basedir) / "config.toml"
    if config_file.is_file():
        try:
            doc = parse(config_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse config.toml: {e}")
    else:
        doc = document()

    if "emby" not in doc or not isinstance(doc["emby"], MutableMapping):
        doc["emby"] = {}
    doc["emby"]["time_range"] = target_config.emby.time_range
    doc["emby"]["interval_days"] = target_config.emby.interval_days
    doc["emby"]["concurrency"] = target_config.emby.concurrency

    if target_config.proxy:
        if "proxy" not in doc or not isinstance(doc["proxy"], MutableMapping):
            doc["proxy"] = {}
        proxy = target_config.proxy
        if proxy.hostname is None:
            doc["proxy"].pop("hostname", None)
        else:
            doc["proxy"]["hostname"] = proxy.hostname
        if proxy.port is None:
            doc["proxy"].pop("port", None)
        else:
            doc["proxy"]["port"] = proxy.port
        if proxy.scheme is None:
            doc["proxy"].pop("scheme", None)
        else:
            doc["proxy"]["scheme"] = proxy.scheme
    else:
        doc.pop("proxy", None)

    try:
        _write_text_atomic(config_file, dumps(doc))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write config.toml: {e}")


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

    new_config = config._cache.model_copy(deep=True)

    fields_set = _model_fields_set(req)

    if "emby_time_range" in fields_set:
        new_config.emby.time_range = req.emby_time_range
    if "emby_interval_days" in fields_set:
        new_config.emby.interval_days = req.emby_interval_days
    if "emby_concurrency" in fields_set:
        new_config.emby.concurrency = req.emby_concurrency

    if "proxy" in fields_set:
        if req.proxy is None:
            new_config.proxy = None
        else:
            existing_proxy = new_config.proxy or ProxyConfig()
            proxy_fields_set = _model_fields_set(req.proxy)
            if "hostname" in proxy_fields_set:
                existing_proxy.hostname = req.proxy.hostname
            if "port" in proxy_fields_set:
                existing_proxy.port = req.proxy.port
            if "scheme" in proxy_fields_set:
                existing_proxy.scheme = req.proxy.scheme
            if (
                existing_proxy.hostname is not None
                or existing_proxy.port is not None
                or existing_proxy.scheme is not None
            ):
                new_config.proxy = existing_proxy
            else:
                new_config.proxy = None

    if new_config.emby.concurrency is not None and new_config.emby.concurrency <= 0:
        raise HTTPException(status_code=400, detail="emby_concurrency must be greater than 0")
    if "emby_time_range" in fields_set or "emby_interval_days" in fields_set:
        validate_schedule_fields(
            new_config.emby.interval_days,
            new_config.emby.time_range,
            use_defaults=False,
        )
    if new_config.proxy is not None:
        try:
            new_config.proxy = ProxyConfig.model_validate(new_config.proxy.model_dump(exclude_none=True))
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=e.errors()[0]["msg"])

    _persist_global_config(new_config)
    if not config.set(new_config):
        raise HTTPException(status_code=400, detail="Invalid config")

    return {"status": "updated"}
