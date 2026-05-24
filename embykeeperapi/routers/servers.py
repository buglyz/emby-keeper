import asyncio
import uuid
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

logger = logger.bind(scheme="embykeeperapi")

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _make_account_id(username: str, name: Optional[str], url: str) -> str:
    """Generate account ID matching EmbyManager.get_spec pattern."""
    # Parse host from URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or url
    return f"{username}@{name or host}"


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
        checkin_plugin_id=data.get("checkin_plugin_id"),
        has_token=status_data.get("has_token", bool(data.get("encrypted_token"))),
        is_online=status_data.get("is_online"),
        last_login_time=status_data.get("last_login_time"),
        last_watch_time=status_data.get("last_watch_time"),
        last_watch_status=status_data.get("last_watch_status"),
        next_schedule_time=status_data.get("next_schedule_time"),
        is_running=status_data.get("is_running", False),
    )


@router.get("", response_model=List[EmbyServerResponse])
async def list_servers(user: str = Depends(get_current_user)):
    """List all Emby server accounts with status."""
    accounts = bridge.web_accounts.get_all()
    return [_account_data_to_response(aid, data) for aid, data in accounts.items()]


@router.get("/{account_id}", response_model=EmbyServerResponse)
async def get_server(account_id: str, user: str = Depends(get_current_user)):
    """Get a single Emby server account detail."""
    data = bridge.web_accounts.get(account_id)
    if not data:
        raise HTTPException(status_code=404, detail="Server not found")
    return _account_data_to_response(account_id, data)


@router.post("", response_model=EmbyServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(req: EmbyServerCreate, user: str = Depends(get_current_user)):
    """Create a new Emby server account."""
    account_id = _make_account_id(req.username, req.name, req.url)

    # Check for duplicate
    if bridge.web_accounts.get(account_id):
        raise HTTPException(status_code=409, detail="Server with this ID already exists")

    # Handle auth method
    if req.auth_method == "token":
        if not req.access_token:
            raise HTTPException(
                status_code=400,
                detail="access_token is required when auth_method is 'token'",
            )
        encrypted_token = encrypt_token(req.access_token, bridge.web_accounts.basedir)
    elif req.auth_method == "password":
        if not req.password:
            raise HTTPException(
                status_code=400,
                detail="password is required when auth_method is 'password'",
            )
        # Exchange password for token via Emby API (one-time use)
        from embykeeper.emby.api import Emby
        from embykeeper.schema import EmbyAccount

        temp_account = EmbyAccount(
            url=req.url,
            username=req.username,
            password=req.password,
            name=req.name,
        )
        emby = Emby(temp_account)
        token_result = await emby.login()
        if not token_result:
            raise HTTPException(
                status_code=400,
                detail="Failed to authenticate with Emby server. Check username and password.",
            )
        # Password is discarded after successful exchange; only token is stored
        encrypted_token = encrypt_token(token_result, bridge.web_accounts.basedir)
        logger.info(f"Successfully exchanged password for token for {account_id}")
    else:
        raise HTTPException(status_code=400, detail="auth_method must be 'token' or 'password'")

    # Store account data
    account_data = {
        "url": req.url,
        "username": req.username,
        "name": req.name,
        "auth_method": req.auth_method,
        "encrypted_token": encrypted_token,
        "time": req.time,
        "allow_multiple": req.allow_multiple,
        "allow_stream": req.allow_stream,
        "use_proxy": req.use_proxy,
        "play_id": req.play_id,
        "enabled": req.enabled,
        "interval_days": req.interval_days,
        "time_range": req.time_range,
        "checkin_plugin_id": req.checkin_plugin_id,
    }

    # Remove None values
    account_data = {k: v for k, v in account_data.items() if v is not None}

    bridge.add_account(account_id, account_data)

    return _account_data_to_response(account_id, bridge.web_accounts.get(account_id))


@router.put("/{account_id}", response_model=EmbyServerResponse)
async def update_server(
    account_id: str,
    req: EmbyServerUpdate,
    user: str = Depends(get_current_user),
):
    """Update an existing Emby server account."""
    existing = bridge.web_accounts.get(account_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Server not found")

    update_data = {}

    # Handle auth method update
    if req.auth_method == "token" and req.access_token:
        update_data["auth_method"] = "token"
        update_data["encrypted_token"] = encrypt_token(
            req.access_token, bridge.web_accounts.basedir
        )
    elif req.auth_method == "password" and req.password:
        # Exchange new password for token
        from embykeeper.emby.api import Emby
        from embykeeper.schema import EmbyAccount

        url = req.url or existing["url"]
        username = req.username or existing["username"]

        temp_account = EmbyAccount(
            url=url,
            username=username,
            password=req.password,
            name=existing.get("name"),
        )
        emby = Emby(temp_account)
        token_result = await emby.login()
        if not token_result:
            raise HTTPException(
                status_code=400,
                detail="Failed to authenticate with Emby server. Check username and password.",
            )
        update_data["auth_method"] = "password"
        update_data["encrypted_token"] = encrypt_token(
            token_result, bridge.web_accounts.basedir
        )
        # Password discarded after exchange

    # Update other fields
    simple_fields = [
        "url", "username", "name", "time", "allow_multiple", "allow_stream",
        "use_proxy", "play_id", "enabled", "interval_days", "time_range",
        "checkin_plugin_id",
    ]
    for field in simple_fields:
        if getattr(req, field) is not None:
            update_data[field] = getattr(req, field)

    bridge.update_account(account_id, update_data)

    return _account_data_to_response(account_id, bridge.web_accounts.get(account_id))


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(account_id: str, user: str = Depends(get_current_user)):
    """Delete an Emby server account."""
    if not bridge.web_accounts.get(account_id):
        raise HTTPException(status_code=404, detail="Server not found")
    bridge.delete_account(account_id)


@router.patch("/{account_id}/toggle", response_model=EmbyServerResponse)
async def toggle_server(
    account_id: str,
    req: EmbyServerToggle,
    user: str = Depends(get_current_user),
):
    """Enable or disable an Emby server account."""
    if not bridge.web_accounts.get(account_id):
        raise HTTPException(status_code=404, detail="Server not found")
    bridge.update_account(account_id, {"enabled": req.enabled})
    return _account_data_to_response(account_id, bridge.web_accounts.get(account_id))


@router.post("/{account_id}/login", response_model=ActionResponse)
async def trigger_login(account_id: str, user: str = Depends(get_current_user)):
    """Trigger an immediate login test."""
    result = await bridge.trigger_login(account_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return ActionResponse(
        run_id=result.get("run_id", ""),
        status=result.get("status", "started"),
        message=result.get("message", ""),
    )


@router.post("/{account_id}/watch", response_model=ActionResponse)
async def trigger_watch(account_id: str, user: str = Depends(get_current_user)):
    """Trigger an immediate simulate-watch."""
    result = await bridge.trigger_watch(account_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return ActionResponse(
        run_id=result.get("run_id", ""),
        status="started",
        message="Watch task started",
    )


@router.post("/{account_id}/checkin", response_model=ActionResponse)
async def trigger_checkin(account_id: str, user: str = Depends(get_current_user)):
    """Trigger a check-in (sign-in) action."""
    result = await bridge.trigger_checkin(account_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return ActionResponse(
        run_id=result.get("run_id", ""),
        status=result.get("status", "started"),
        message=result.get("message", ""),
    )


@router.post("/actions/watch-all", response_model=ActionResponse)
async def watch_all(user: str = Depends(get_current_user)):
    """Trigger watch for all enabled accounts."""
    if not bridge.emby_manager:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")

    from embykeeper.runinfo import RunContext, RunStatus
    ctx = RunContext.prepare(description="Manual watch all")

    task = asyncio.create_task(
        bridge.emby_manager._watch_main(
            bridge.web_accounts.to_emby_accounts(),
            instant=True,
        ),
        name="watch-all",
    )

    return ActionResponse(
        run_id=ctx.id,
        status="started",
        message="Watch all task started",
    )