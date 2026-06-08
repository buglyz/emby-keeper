from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from ..auth import get_current_user
from ..models import (
    EmbyServerCreate,
    EmbyServerUpdate,
    EmbyServerResponse,
    EmbyServerToggle,
    ActionResponse,
)
from ..scheduler_bridge import bridge
from ..crypto import encrypt_token
from ..validation import validate_schedule_fields
from embykeeper.config import config
from embykeeper.schema import EmbyConfig

logger = logger.bind(scheme="embykeeperapi")

router = APIRouter(prefix="/api/servers", tags=["servers"])

EMBY_ACCOUNT_ENV_FIELDS = [
    "use_proxy",
    "useragent",
    "client",
    "client_version",
    "device",
    "device_id",
]
OPTIONAL_TEXT_FIELDS = {
    "name",
    "play_id",
    "useragent",
    "client",
    "client_version",
    "device",
    "device_id",
}
SCHEDULE_TEXT_FIELDS = {"interval_days", "time_range"}
BOOLEAN_FIELDS = {"allow_multiple", "allow_stream", "use_proxy", "enabled"}
AUTH_METHODS = {"token", "password"}


def _make_account_id(username: str, name: Optional[str], url: str) -> str:
    """Generate account ID matching EmbyManager.get_spec pattern."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or url
    return f"{username}@{name or host}"


def _model_fields_set(model) -> set:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is not None:
        return fields_set
    return getattr(model, "__fields_set__", set())


def _validate_server_fields(url: Optional[str] = None, time=None):
    """Validate common server fields."""
    if url is not None:
        from urllib.parse import urlparse

        if any(ch.isspace() for ch in url):
            raise HTTPException(status_code=400, detail="URL must not contain whitespace")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
        if not parsed.hostname:
            raise HTTPException(status_code=400, detail="URL must contain a valid hostname")
        try:
            parsed.port
        except ValueError:
            raise HTTPException(status_code=400, detail="URL must contain a valid port")
        if parsed.username or parsed.password:
            raise HTTPException(status_code=400, detail="URL must not contain username or password")
        if parsed.query or parsed.fragment:
            raise HTTPException(status_code=400, detail="URL must not contain query or fragment")

    if time is not None:
        if isinstance(time, (list, tuple)):
            if len(time) != 2:
                raise HTTPException(status_code=400, detail="time must be an integer or a [min, max] pair")
            if any(not isinstance(value, int) or isinstance(value, bool) for value in time):
                raise HTTPException(status_code=400, detail="time values must be integers")
            if time[0] <= 0 or time[1] <= 0:
                raise HTTPException(status_code=400, detail="time values must be positive")
            if time[0] > time[1]:
                raise HTTPException(status_code=400, detail="time[0] (min) must be <= time[1] (max)")
        elif isinstance(time, int):
            if isinstance(time, bool):
                raise HTTPException(status_code=400, detail="time must be an integer")
            if time <= 0:
                raise HTTPException(status_code=400, detail="time must be positive")
        else:
            raise HTTPException(status_code=400, detail="time must be an integer or a [min, max] pair")


def _validate_required_text(field: str, value: Optional[str]):
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    if not value.strip():
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    return value.strip()


def _normalize_optional_text(value: Optional[str], field: str = "value"):
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    value = value.strip()
    return value or None


def _normalize_bool(value, field: str, *, required: bool = False):
    if value is None and not required:
        return None
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be a boolean")
    return value


def _normalize_auth_method(value):
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="auth_method must be a string")
    value = value.strip().lower()
    if value not in AUTH_METHODS:
        raise HTTPException(status_code=400, detail="auth_method must be 'token' or 'password'")
    return value


def _has_supplied_credential(source, fields_set: set, field: str) -> bool:
    if field not in fields_set:
        return False
    value = getattr(source, field)
    if value is None:
        return False
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return bool(value.strip())


def _validate_credential_selection(auth_method: str, has_access_token: bool, has_password: bool):
    if has_access_token and has_password:
        raise HTTPException(status_code=400, detail="Provide either access_token or password, not both")
    if auth_method == "token" and has_password:
        raise HTTPException(status_code=400, detail="password cannot be supplied when auth_method is 'token'")
    if auth_method == "password" and has_access_token:
        raise HTTPException(
            status_code=400, detail="access_token cannot be supplied when auth_method is 'password'"
        )


def _normalized_optional_text_updates(source) -> dict:
    return {
        field: _normalize_optional_text(getattr(source, field), field)
        for field in OPTIONAL_TEXT_FIELDS
        if hasattr(source, field)
    }


def _validate_account_schedule(interval_days=None, time_range=None):
    if interval_days is None and time_range is None:
        return
    emby_config = config._cache.emby if config._cache and config._cache.emby else EmbyConfig()
    validate_schedule_fields(
        interval_days if interval_days is not None else emby_config.interval_days,
        time_range if time_range is not None else emby_config.time_range,
    )


def _make_temp_account_kwargs(
    *,
    url: str,
    username: str,
    password: str,
    name: Optional[str] = None,
    source=None,
    existing: Optional[dict] = None,
    updates: Optional[dict] = None,
) -> dict:
    temp_kwargs = {
        "url": url,
        "username": username,
        "password": password,
    }
    if name is not None:
        temp_kwargs["name"] = name

    for field in EMBY_ACCOUNT_ENV_FIELDS:
        value = None
        if updates and field in updates:
            value = updates[field]
        elif existing and field in existing:
            value = existing[field]
        elif source is not None:
            value = getattr(source, field, None)
        if value is not None:
            temp_kwargs[field] = value

    return temp_kwargs


def _account_data_to_response(account_id: str, data: dict) -> EmbyServerResponse:
    """Convert internal account data to API response (never includes secrets)."""
    status_data = bridge.get_account_status(account_id)
    return EmbyServerResponse(
        id=account_id,
        url=data.get("url", ""),
        username=data.get("username", ""),
        name=data.get("name"),
        auth_method=data.get("auth_method", "token"),
        time=data.get("time", [300, 600]),
        allow_multiple=data.get("allow_multiple", True),
        allow_stream=data.get("allow_stream", False),
        use_proxy=data.get("use_proxy", True),
        play_id=data.get("play_id"),
        enabled=data.get("enabled", True),
        interval_days=data.get("interval_days"),
        time_range=data.get("time_range"),
        useragent=data.get("useragent"),
        client=data.get("client"),
        client_version=data.get("client_version"),
        device=data.get("device"),
        device_id=data.get("device_id"),
        has_token=status_data.get("has_token", bool(data.get("encrypted_token"))),
        is_online=status_data.get("is_online"),
        last_login_time=status_data.get("last_login_time"),
        last_watch_time=status_data.get("last_watch_time"),
        last_watch_status=status_data.get("last_watch_status"),
        next_schedule_time=status_data.get("next_schedule_time"),
        is_running=status_data.get("is_running", False),
    )


def _require_bridge():
    """Ensure the scheduler bridge is initialized."""
    if bridge.web_accounts is None:
        raise HTTPException(status_code=503, detail="Service initializing, please retry")


@router.get("", response_model=List[EmbyServerResponse])
async def list_servers(user: str = Depends(get_current_user)):
    """List all Emby server accounts with status."""
    _require_bridge()
    accounts = bridge.web_accounts.get_all()
    return [_account_data_to_response(aid, data) for aid, data in accounts.items()]


@router.get("/{account_id:path}", response_model=EmbyServerResponse)
async def get_server(account_id: str, user: str = Depends(get_current_user)):
    """Get a single Emby server account detail."""
    _require_bridge()
    data = bridge.web_accounts.get(account_id)
    if not data:
        raise HTTPException(status_code=404, detail="Server not found")
    return _account_data_to_response(account_id, data)


@router.post("", response_model=EmbyServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(req: EmbyServerCreate, user: str = Depends(get_current_user)):
    """Create a new Emby server account."""
    _require_bridge()
    url = _validate_required_text("url", req.url)
    username = _validate_required_text("username", req.username)
    optional_text = _normalized_optional_text_updates(req)
    interval_days = _normalize_optional_text(req.interval_days, "interval_days")
    time_range = _normalize_optional_text(req.time_range, "time_range")
    bool_values = {field: _normalize_bool(getattr(req, field), field) for field in BOOLEAN_FIELDS}
    auth_method = _normalize_auth_method(req.auth_method)
    fields_set = _model_fields_set(req)
    has_access_token = _has_supplied_credential(req, fields_set, "access_token")
    has_password = _has_supplied_credential(req, fields_set, "password")
    _validate_credential_selection(auth_method, has_access_token, has_password)
    name = optional_text["name"]
    _validate_server_fields(url=url, time=req.time)
    _validate_account_schedule(interval_days, time_range)
    account_id = _make_account_id(username, name, url)

    # Check for duplicate
    if bridge.web_accounts.get(account_id):
        raise HTTPException(status_code=409, detail="Server with this ID already exists")

    # Handle auth method
    if auth_method == "token":
        access_token = _validate_required_text("access_token", req.access_token)
        encrypted_token = encrypt_token(access_token, bridge.web_accounts.basedir)
        user_id = None
    elif auth_method == "password":
        _validate_required_text("password", req.password)
        # Exchange password for token via Emby API (one-time use)
        from embykeeper.emby.api import Emby
        from embykeeper.schema import EmbyAccount

        temp_kwargs = _make_temp_account_kwargs(
            url=url,
            username=username,
            password=req.password,
            name=name,
            source=req,
            updates=optional_text,
        )
        temp_account = EmbyAccount(**temp_kwargs)
        emby = Emby(temp_account)
        try:
            token_result = await emby.login()
        except Exception as e:
            logger.warning(f"Emby login failed for {account_id}: {type(e).__name__}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to connect to Emby server: {type(e).__name__}",
            )
        if not token_result:
            raise HTTPException(
                status_code=400,
                detail="Failed to authenticate with Emby server. Check username and password.",
            )
        encrypted_token = encrypt_token(token_result, bridge.web_accounts.basedir)
        user_id = emby.user_id
        logger.info(f"Successfully exchanged password for token for {account_id}")
    # Store account data
    account_data = {
        "url": url,
        "username": username,
        "name": name,
        "auth_method": auth_method,
        "encrypted_token": encrypted_token,
        "user_id": user_id,
        "time": req.time,
        "allow_multiple": bool_values["allow_multiple"],
        "allow_stream": bool_values["allow_stream"],
        "use_proxy": bool_values["use_proxy"],
        "play_id": optional_text["play_id"],
        "enabled": bool_values["enabled"],
        "interval_days": interval_days,
        "time_range": time_range,
        "useragent": optional_text["useragent"],
        "client": optional_text["client"],
        "client_version": optional_text["client_version"],
        "device": optional_text["device"],
        "device_id": optional_text["device_id"],
    }

    # Remove None values
    account_data = {k: v for k, v in account_data.items() if v is not None}

    bridge.add_account(account_id, account_data)

    return _account_data_to_response(account_id, bridge.web_accounts.get(account_id))


@router.put("/{account_id:path}", response_model=EmbyServerResponse)
async def update_server(
    account_id: str,
    req: EmbyServerUpdate,
    user: str = Depends(get_current_user),
):
    """Update an existing Emby server account."""
    _require_bridge()
    existing = bridge.web_accounts.get(account_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Server not found")

    _validate_server_fields(time=req.time)

    update_data = {}
    fields_set = _model_fields_set(req)

    simple_fields = [
        "url",
        "username",
        "name",
        "time",
        "allow_multiple",
        "allow_stream",
        "use_proxy",
        "play_id",
        "enabled",
        "interval_days",
        "time_range",
        "useragent",
        "client",
        "client_version",
        "device",
        "device_id",
    ]
    for field in simple_fields:
        if field in fields_set:
            value = getattr(req, field)
            if field == "url":
                value = _validate_required_text(field, value)
                _validate_server_fields(url=value)
            elif field == "username":
                value = _validate_required_text(field, value)
            elif field in OPTIONAL_TEXT_FIELDS:
                value = _normalize_optional_text(value, field)
            elif field in SCHEDULE_TEXT_FIELDS:
                value = _normalize_optional_text(value, field)
            elif field in BOOLEAN_FIELDS:
                value = _normalize_bool(value, field)
            update_data[field] = value

    if "interval_days" in fields_set or "time_range" in fields_set:
        _validate_account_schedule(
            update_data.get("interval_days", existing.get("interval_days")),
            update_data.get("time_range", existing.get("time_range")),
        )

    auth_method = _normalize_auth_method(req.auth_method) if "auth_method" in fields_set else None
    has_access_token = _has_supplied_credential(req, fields_set, "access_token")
    has_password = _has_supplied_credential(req, fields_set, "password")
    if auth_method is not None:
        requested_auth_method = auth_method
    elif has_access_token:
        requested_auth_method = "token"
    elif has_password:
        requested_auth_method = "password"
    else:
        requested_auth_method = existing.get("auth_method", "token")
    _validate_credential_selection(requested_auth_method, has_access_token, has_password)
    if auth_method is not None:
        if auth_method != existing.get("auth_method", "token") and not (has_access_token or has_password):
            raise HTTPException(
                status_code=400, detail="New credentials are required when changing auth_method"
            )

    if "access_token" in fields_set:
        access_token = _validate_required_text("access_token", req.access_token)
        update_data["auth_method"] = "token"
        update_data["encrypted_token"] = encrypt_token(access_token, bridge.web_accounts.basedir)
        update_data["user_id"] = None
    elif auth_method == "token" and existing.get("auth_method", "token") != "token":
        raise HTTPException(status_code=400, detail="access_token is required when auth_method is 'token'")

    if "password" in fields_set:
        _validate_required_text("password", req.password)
        from embykeeper.emby.api import Emby
        from embykeeper.schema import EmbyAccount

        url = update_data.get("url") or existing["url"]
        username = update_data.get("username") or existing["username"]
        name = update_data.get("name", existing.get("name"))

        temp_kwargs = _make_temp_account_kwargs(
            url=url,
            username=username,
            password=req.password,
            name=name,
            existing=existing,
            updates=update_data,
        )
        temp_account = EmbyAccount(**temp_kwargs)
        emby = Emby(temp_account)
        try:
            token_result = await emby.login()
        except Exception as e:
            logger.warning(f"Emby login failed during update for {account_id}: {type(e).__name__}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to connect to Emby server: {type(e).__name__}",
            )
        if not token_result:
            raise HTTPException(
                status_code=400,
                detail="Failed to authenticate with Emby server. Check username and password.",
            )
        update_data["auth_method"] = "password"
        update_data["encrypted_token"] = encrypt_token(token_result, bridge.web_accounts.basedir)
        update_data["user_id"] = emby.user_id
    elif auth_method == "password" and existing.get("auth_method", "token") != "password":
        raise HTTPException(status_code=400, detail="password is required when auth_method is 'password'")

    new_account_id = _make_account_id(
        update_data.get("username", existing["username"]),
        update_data.get("name", existing.get("name")),
        update_data.get("url", existing["url"]),
    )
    if new_account_id != account_id and bridge.web_accounts.get(new_account_id):
        raise HTTPException(status_code=409, detail="Server with this ID already exists")

    updated_id = bridge.update_account(account_id, update_data, new_account_id)
    if not updated_id:
        raise HTTPException(status_code=409, detail="Server with this ID already exists")

    return _account_data_to_response(updated_id, bridge.web_accounts.get(updated_id))


@router.delete("/{account_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(account_id: str, user: str = Depends(get_current_user)):
    """Delete an Emby server account."""
    _require_bridge()
    if not bridge.web_accounts.get(account_id):
        raise HTTPException(status_code=404, detail="Server not found")
    bridge.delete_account(account_id)


@router.patch("/{account_id:path}/toggle", response_model=EmbyServerResponse)
async def toggle_server(
    account_id: str,
    req: EmbyServerToggle,
    user: str = Depends(get_current_user),
):
    """Enable or disable an Emby server account."""
    _require_bridge()
    if not bridge.web_accounts.get(account_id):
        raise HTTPException(status_code=404, detail="Server not found")
    enabled = _normalize_bool(req.enabled, "enabled", required=True)
    bridge.update_account(account_id, {"enabled": enabled})
    return _account_data_to_response(account_id, bridge.web_accounts.get(account_id))


@router.post("/{account_id:path}/login", response_model=ActionResponse)
async def trigger_login(account_id: str, user: str = Depends(get_current_user)):
    """Trigger an immediate login test."""
    _require_bridge()
    result = await bridge.trigger_login(account_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return ActionResponse(
        run_id=result.get("run_id", ""),
        status=result.get("status", "started"),
        message=result.get("message", ""),
    )


@router.post("/{account_id:path}/watch", response_model=ActionResponse)
async def trigger_watch(account_id: str, user: str = Depends(get_current_user)):
    """Trigger an immediate simulate-watch."""
    _require_bridge()
    result = await bridge.trigger_watch(account_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return ActionResponse(
        run_id=result.get("run_id", ""),
        status=result.get("status", "started"),
        message=result.get("message", "Watch task started"),
    )


@router.post("/actions/watch-all", response_model=ActionResponse)
async def watch_all(user: str = Depends(get_current_user)):
    """Trigger watch for all enabled accounts."""
    _require_bridge()
    result = await bridge.trigger_watch_many()
    return ActionResponse(
        run_id=result.get("run_id", ""),
        status=result.get("status", "started"),
        message=result.get("message", ""),
    )
