from pathlib import Path
from collections.abc import MutableMapping
from tempfile import NamedTemporaryFile
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import ValidationError
from tomlkit import document, dumps, parse

from ..auth import get_current_user
from ..models import GlobalConfigResponse, GlobalConfigUpdate, NotifierConfigResponse, NotifierConfigUpdate
from ..validation import validate_schedule_fields
from embykeeper.config import config
from embykeeper.apprise import AppriseStream
from embykeeper.schema import EmbyConfig, NotifierConfig, ProxyConfig

router = APIRouter(prefix="/api/config", tags=["config"])
logger = logger.bind(scheme="embykeeperapi")


def _model_fields_set(model) -> set:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is not None:
        return fields_set
    return getattr(model, "__fields_set__", set())


def _normalize_schedule_text(field: str, value):
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    return value


def _normalize_optional_positive_int(field: str, value):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    if value <= 0:
        raise HTTPException(status_code=400, detail=f"{field} must be greater than 0")
    return value


def _normalize_optional_text(field: str, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    value = value.strip()
    return value or None


def _telegram_uri(bot_token: str, chat_id: str) -> str:
    token = quote(bot_token, safe=":")
    target = quote(chat_id, safe="@:-")
    return f"tgram://{token}/{target}"


def _telegram_chat_id_from_uri(uri: str):
    if not isinstance(uri, str) or not uri.startswith("tgram://"):
        return None
    parsed = urlparse(uri)
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    return path_parts[1] if len(path_parts) >= 2 else (path_parts[0] if path_parts else None)


def _notifier_response() -> NotifierConfigResponse:
    notifier = config._cache.notifier if config._cache and config._cache.notifier else NotifierConfig()
    uri = notifier.apprise_uri
    telegram_chat_id = _telegram_chat_id_from_uri(uri)
    if telegram_chat_id:
        target_label = f"Telegram: {telegram_chat_id}"
    elif uri:
        target_label = "Apprise URI configured"
    else:
        target_label = None
    return NotifierConfigResponse(
        enabled=bool(notifier.enabled),
        method="telegram" if telegram_chat_id else (notifier.method or "apprise"),
        configured=bool(uri),
        target_label=target_label,
        telegram_chat_id=telegram_chat_id,
    )


async def _refresh_notifier():
    try:
        from embykeeper.notify import start_notifier

        await start_notifier()
    except Exception as e:
        logger.warning(f"Failed to refresh notifier: {type(e).__name__}")


def _set_toml_value(table, key: str, value):
    if value is None:
        table.pop(key, None)
    else:
        table[key] = value


def _write_text_atomic(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
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
            tmp_path = Path(tmp.name)
            tmp.write(content)
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        tmp_path.replace(path)
    except Exception:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _persist_global_config(next_config=None):
    """Persist Web UI global settings without rewriting account secrets."""
    target_config = next_config or config._cache
    config_file = Path(config._conf_file) if config._conf_file else Path(config.basedir) / "config.toml"
    if config_file.is_file():
        try:
            doc = parse(config_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse config.toml: {e}")
    else:
        doc = document()

    if "emby" not in doc or not isinstance(doc["emby"], MutableMapping):
        doc["emby"] = {}
    emby = target_config.emby or EmbyConfig()
    _set_toml_value(doc["emby"], "time_range", emby.time_range)
    _set_toml_value(doc["emby"], "interval_days", emby.interval_days)
    _set_toml_value(doc["emby"], "concurrency", emby.concurrency)

    if target_config.proxy:
        if "proxy" not in doc or not isinstance(doc["proxy"], MutableMapping):
            doc["proxy"] = {}
        proxy = target_config.proxy
        _set_toml_value(doc["proxy"], "hostname", proxy.hostname)
        _set_toml_value(doc["proxy"], "port", proxy.port)
        _set_toml_value(doc["proxy"], "scheme", proxy.scheme)
    else:
        doc.pop("proxy", None)

    notifier = target_config.notifier
    if notifier:
        if "notifier" not in doc or not isinstance(doc["notifier"], MutableMapping):
            doc["notifier"] = {}
        _set_toml_value(doc["notifier"], "enabled", notifier.enabled)
        _set_toml_value(doc["notifier"], "method", notifier.method)
        _set_toml_value(doc["notifier"], "apprise_uri", notifier.apprise_uri)
    else:
        doc.pop("notifier", None)

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
        emby_time_range=emby.time_range if emby else None,
        emby_interval_days=emby.interval_days if emby else None,
        emby_concurrency=emby.concurrency if emby else None,
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
    if new_config.emby is None:
        new_config.emby = EmbyConfig()

    fields_set = _model_fields_set(req)

    if "emby_time_range" in fields_set:
        new_config.emby.time_range = _normalize_schedule_text("emby_time_range", req.emby_time_range)
    if "emby_interval_days" in fields_set:
        new_config.emby.interval_days = _normalize_schedule_text("emby_interval_days", req.emby_interval_days)
    if "emby_concurrency" in fields_set:
        new_config.emby.concurrency = _normalize_optional_positive_int(
            "emby_concurrency", req.emby_concurrency
        )

    if "proxy" in fields_set:
        if req.proxy is None:
            new_config.proxy = None
        else:
            existing_proxy = new_config.proxy or ProxyConfig()
            proxy_fields_set = _model_fields_set(req.proxy)
            if "hostname" in proxy_fields_set:
                if req.proxy.hostname is None:
                    hostname = None
                elif not isinstance(req.proxy.hostname, str):
                    raise HTTPException(status_code=400, detail="proxy.hostname must be a string")
                else:
                    hostname = req.proxy.hostname.strip()
                existing_proxy.hostname = hostname or None
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
    if not config.set(new_config, preserve_conf_file=True):
        raise HTTPException(status_code=400, detail="Invalid config")

    return {"status": "updated"}


@router.get("/notifier", response_model=NotifierConfigResponse)
async def get_notifier_config(user: str = Depends(get_current_user)):
    """Read notification settings without exposing secret tokens."""
    if not config._cache:
        return NotifierConfigResponse()
    return _notifier_response()


@router.put("/notifier", response_model=NotifierConfigResponse)
async def update_notifier_config(req: NotifierConfigUpdate, user: str = Depends(get_current_user)):
    """Update notification settings. Telegram is stored as an Apprise URI."""
    if not config._cache:
        raise HTTPException(status_code=503, detail="Config not loaded")

    fields_set = _model_fields_set(req)
    new_config = config._cache.model_copy(deep=True)
    existing = new_config.notifier or NotifierConfig()
    enabled = existing.enabled if "enabled" not in fields_set else req.enabled
    if enabled is not None and not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be a boolean")

    method = _normalize_optional_text("method", req.method) if "method" in fields_set else existing.method
    method = (method or "apprise").lower()
    if method not in {"apprise", "telegram"}:
        raise HTTPException(status_code=400, detail="method must be 'apprise' or 'telegram'")

    uri = existing.apprise_uri
    if req.clear:
        uri = None

    bot_token = _normalize_optional_text("telegram_bot_token", req.telegram_bot_token)
    chat_id = _normalize_optional_text("telegram_chat_id", req.telegram_chat_id)
    apprise_uri = _normalize_optional_text("apprise_uri", req.apprise_uri)

    if bot_token or chat_id:
        if not bot_token or not chat_id:
            raise HTTPException(
                status_code=400, detail="telegram_bot_token and telegram_chat_id are required"
            )
        uri = _telegram_uri(bot_token, chat_id)
        method = "apprise"
    elif apprise_uri is not None:
        uri = apprise_uri
        method = "apprise"

    if enabled and not uri:
        raise HTTPException(status_code=400, detail="Notification target is required when enabled")

    new_config.notifier = NotifierConfig(enabled=bool(enabled), method="apprise", apprise_uri=uri)
    _persist_global_config(new_config)
    if not config.set(new_config, preserve_conf_file=True):
        raise HTTPException(status_code=400, detail="Invalid config")
    await _refresh_notifier()
    return _notifier_response()


@router.post("/notifier/test")
async def test_notifier(req: NotifierConfigUpdate, user: str = Depends(get_current_user)):
    """Send a test notification to the provided or currently configured target."""
    uri = None
    bot_token = _normalize_optional_text("telegram_bot_token", req.telegram_bot_token)
    chat_id = _normalize_optional_text("telegram_chat_id", req.telegram_chat_id)
    apprise_uri = _normalize_optional_text("apprise_uri", req.apprise_uri)
    if bot_token or chat_id:
        if not bot_token or not chat_id:
            raise HTTPException(
                status_code=400, detail="telegram_bot_token and telegram_chat_id are required"
            )
        uri = _telegram_uri(bot_token, chat_id)
    elif apprise_uri:
        uri = apprise_uri
    elif config._cache and config._cache.notifier:
        uri = config._cache.notifier.apprise_uri
    if not uri:
        raise HTTPException(status_code=400, detail="Notification target is required")

    stream = AppriseStream(uri)
    if not getattr(stream, "ready", True):
        raise HTTPException(status_code=400, detail="Notification target is invalid")
    stream.write("INFO#Emby Keeper notification test")
    await stream.join()
    return {"status": "sent"}
