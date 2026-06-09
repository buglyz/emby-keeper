from typing import List

from fastapi import APIRouter, Depends

from embykeeper.config import config

from ..auth import get_current_user
from ..config_service import ConfigService
from ..models import (
    AutomationConfigResponse,
    AutomationConfigUpdate,
    ConfigBackupResponse,
    ConfigBackupItem,
    ConfigExportResponse,
    ConfigRestoreRequest,
    ConfigRestoreResponse,
    GlobalConfigResponse,
    GlobalConfigUpdate,
    NotifierConfigResponse,
    NotifierConfigUpdate,
)
from ..scheduler_bridge import bridge

router = APIRouter(prefix="/api/config", tags=["config"])
config_service = ConfigService(config, bridge)


@router.get("", response_model=GlobalConfigResponse)
async def get_config(user: str = Depends(get_current_user)):
    """Read current global config."""
    return config_service.get_global_config()


@router.put("")
async def update_config(req: GlobalConfigUpdate, user: str = Depends(get_current_user)):
    """Update global config settings."""
    return config_service.update_global_config(req)


@router.get("/automation", response_model=AutomationConfigResponse)
async def get_automation_config(user: str = Depends(get_current_user)):
    """Read Telegram check-in and timed registrar settings."""
    return config_service.get_automation_config()


@router.put("/automation", response_model=AutomationConfigResponse)
async def update_automation_config(req: AutomationConfigUpdate, user: str = Depends(get_current_user)):
    """Update Telegram check-in and timed registrar settings."""
    return await config_service.update_automation_config(req)


@router.get("/notifier", response_model=NotifierConfigResponse)
async def get_notifier_config(user: str = Depends(get_current_user)):
    """Read notification settings without exposing secret tokens."""
    return config_service.get_notifier_config()


@router.put("/notifier", response_model=NotifierConfigResponse)
async def update_notifier_config(req: NotifierConfigUpdate, user: str = Depends(get_current_user)):
    """Update notification settings."""
    return await config_service.update_notifier_config(req)


@router.post("/notifier/test")
async def test_notifier(req: NotifierConfigUpdate, user: str = Depends(get_current_user)):
    """Send a test notification to the provided or currently configured target."""
    return await config_service.test_notifier(req)


@router.get("/export", response_model=ConfigExportResponse)
async def export_config_bundle(user: str = Depends(get_current_user)):
    """Export a redacted config snapshot for diagnostics."""
    return config_service.export_config_bundle()


@router.post("/backup", response_model=ConfigBackupResponse)
async def create_config_backup(user: str = Depends(get_current_user)):
    """Create a local timestamped backup of config.toml and web_accounts.json."""
    return config_service.create_backup_snapshot()


@router.get("/backups", response_model=List[ConfigBackupItem])
async def list_config_backups(user: str = Depends(get_current_user)):
    """List local config backups available for restore."""
    return config_service.list_config_backups()


@router.post("/backups/{backup_id}/restore", response_model=ConfigRestoreResponse)
async def restore_config_backup(
    backup_id: str,
    req: ConfigRestoreRequest,
    user: str = Depends(get_current_user),
):
    """Restore config files from a local backup."""
    return await config_service.restore_config_backup(backup_id, req)
