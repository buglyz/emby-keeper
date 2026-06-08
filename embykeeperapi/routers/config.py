from pathlib import Path
from collections.abc import MutableMapping
from datetime import datetime, timezone
import shutil
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qsl, quote, unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import ValidationError
from tomlkit import document, dumps, parse

from ..auth import get_current_user
from ..models import (
    ConfigBackupResponse,
    ConfigExportResponse,
    GlobalConfigResponse,
    GlobalConfigUpdate,
    NotifierConfigResponse,
    NotifierConfigUpdate,
)
from ..validation import validate_schedule_fields
from ..scheduler_bridge import bridge
from embykeeper.config import config
from embykeeper.apprise import AppriseStream
from embykeeper.schema import EmbyConfig, NotifierConfig, ProxyConfig

router = APIRouter(prefix="/api/config", tags=["config"])
logger = logger.bind(scheme="embykeeperapi")
REDACTED_VALUE = "***REDACTED***"
SECRET_CONFIG_KEYS = {"password", "apprise_uri", "mongodb", "token", "access_token", "encrypted_token", "secret"}
SECRET_CONFIG_KEY_PARTS = ("token", "secret", "password", "credential", "apikey", "api_key")


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


def _is_telegram_uri(uri: str) -> bool:
    return bool(_telegram_chat_id_from_uri(uri))


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


def _config_file_path() -> Path:
    return Path(config._conf_file) if config._conf_file else Path(config.basedir) / "config.toml"


def _web_accounts_file_path() -> Path:
    basedir = getattr(bridge.web_accounts, "basedir", None)
    if basedir:
        return Path(basedir) / "web_accounts.json"
    return Path(config.basedir) / "web_accounts.json"


def _is_secret_key(key) -> bool:
    normalized = str(key).lower()
    return normalized in SECRET_CONFIG_KEYS or any(part in normalized for part in SECRET_CONFIG_KEY_PARTS)


def _is_sensitive_url(value: str) -> bool:
    parsed = urlparse(value)
    if not parsed.scheme:
        return False
    if parsed.username or parsed.password:
        return True
    return any(_is_secret_key(key) for key, _value in parse_qsl(parsed.query, keep_blank_values=True))


def _redact_scalar_value(value):
    if isinstance(value, str) and _is_sensitive_url(value):
        return REDACTED_VALUE
    return value


def _redact_toml_value(value):
    if isinstance(value, MutableMapping):
        for key in list(value.keys()):
            if _is_secret_key(key):
                value[key] = REDACTED_VALUE
            else:
                child = value[key]
                if isinstance(child, (MutableMapping, list)):
                    _redact_toml_value(child)
                else:
                    value[key] = _redact_scalar_value(child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (MutableMapping, list)):
                _redact_toml_value(item)
            else:
                value[index] = _redact_scalar_value(item)


def _redact_config_toml(content: str) -> str:
    doc = parse(content)
    _redact_toml_value(doc)
    return dumps(doc)


def _redact_web_accounts(accounts: dict) -> dict:
    return _redact_plain_mapping(accounts)


def _redact_plain_mapping(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            redacted[key] = (
                REDACTED_VALUE
                if _is_secret_key(key)
                else _redact_plain_mapping(_redact_scalar_value(item))
            )
        return redacted
    if isinstance(value, list):
        return [_redact_plain_mapping(item) for item in value]
    return _redact_scalar_value(value)


def _persist_global_config(next_config=None):
    """Persist Web UI global settings without rewriting account secrets."""
    target_config = next_config or config._cache
    config_file = _config_file_path()
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


@router.get("/export", response_model=ConfigExportResponse)
async def export_config_bundle(user: str = Depends(get_current_user)):
    """Export a redacted config snapshot for diagnostics."""
    config_file = _config_file_path()
    accounts_file = _web_accounts_file_path()
    config_toml = None
    if config_file.is_file():
        try:
            config_toml = _redact_config_toml(config_file.read_text(encoding="utf-8"))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to redact config.toml: {e}")
    web_accounts = {}
    if bridge.web_accounts:
        try:
            web_accounts = _redact_web_accounts(bridge.web_accounts.get_all())
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to read web accounts: {e}")
    return ConfigExportResponse(
        generated_at=datetime.now(timezone.utc),
        config_path=str(config_file),
        web_accounts_path=str(accounts_file),
        config_toml=config_toml,
        web_accounts=web_accounts,
        redacted=True,
    )


@router.post("/backup", response_model=ConfigBackupResponse)
async def create_config_backup(user: str = Depends(get_current_user)):
    """Create a local timestamped backup of config.toml and web_accounts.json."""
    basedir = Path(config.basedir)
    backup_root = basedir / "backups"
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_root.chmod(0o700)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to prepare backup directory: {e}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backup_root / timestamp
    for counter in range(100):
        candidate = backup_dir if counter == 0 else backup_root / f"{timestamp}-{counter}"
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            try:
                candidate.chmod(0o700)
            except OSError:
                pass
            backup_dir = candidate
            break
        except FileExistsError:
            continue
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to create backup directory: {e}")
    else:
        raise HTTPException(status_code=500, detail="Failed to create unique backup directory")

    copied = []
    try:
        for source in (_config_file_path(), _web_accounts_file_path()):
            if not source.is_file():
                continue
            target = backup_dir / source.name
            shutil.copy2(source, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
            copied.append(source.name)
    except OSError as e:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to back up {source.name}: {e}")

    if not copied:
        try:
            backup_dir.rmdir()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to remove empty backup directory: {e}")
        raise HTTPException(status_code=404, detail="No config files found to back up")

    return ConfigBackupResponse(status="created", backup_dir=str(backup_dir), files=copied)


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
    existing_is_telegram = _is_telegram_uri(uri)
    if req.clear:
        uri = None
        existing_is_telegram = False

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
    elif "method" in fields_set and enabled:
        if method == "telegram" and not existing_is_telegram:
            raise HTTPException(status_code=400, detail="Telegram target is required")
        if method == "apprise" and existing_is_telegram:
            raise HTTPException(status_code=400, detail="Apprise URI is required")

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
