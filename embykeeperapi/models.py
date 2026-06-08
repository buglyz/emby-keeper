from datetime import datetime
from typing import List, Optional, Union

from pydantic import BaseModel, Field, StrictInt

WatchTime = Union[StrictInt, List[StrictInt]]


# ============ Auth Models ============


class TokenExchangeRequest(BaseModel):
    token: str


class PasswordLoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ============ Emby Server (Account) Models ============


class EmbyServerCreate(BaseModel):
    """Create a new Emby server account."""

    url: str = Field(description="Emby server URL, e.g. https://emby.example.com:8096")
    username: str = Field(description="Emby username")
    auth_method: str = Field(default="token", description="Authentication method: 'token' or 'password'")
    # For token auth: direct AccessToken
    access_token: Optional[str] = Field(default=None, description="AccessToken (for token auth method)")
    # For password auth: will be exchanged for token
    password: Optional[str] = Field(
        default=None, description="Password (for password auth method, one-time use)"
    )
    name: Optional[str] = Field(default=None, description="Display name for this server")
    time: Optional[WatchTime] = Field(
        default_factory=lambda: [300, 600], description="Watch duration range (seconds)"
    )
    allow_multiple: Optional[bool] = Field(default=True, description="Allow playing multiple videos")
    allow_stream: Optional[bool] = Field(default=False, description="Allow streaming when no length info")
    use_proxy: Optional[bool] = Field(default=True, description="Use configured proxy")
    play_id: Optional[str] = Field(default=None, description="Specific video ID to play")
    enabled: Optional[bool] = Field(default=True, description="Whether this account is enabled")
    interval_days: Optional[str] = Field(default=None, description="Per-account interval override")
    time_range: Optional[str] = Field(default=None, description="Per-account time range override")
    # Advanced settings
    useragent: Optional[str] = None
    client: Optional[str] = None
    client_version: Optional[str] = None
    device: Optional[str] = None
    device_id: Optional[str] = None


class EmbyServerUpdate(BaseModel):
    """Update an existing Emby server account (all fields optional)."""

    url: Optional[str] = None
    username: Optional[str] = None
    auth_method: Optional[str] = None
    access_token: Optional[str] = None
    password: Optional[str] = None
    name: Optional[str] = None
    time: Optional[WatchTime] = None
    allow_multiple: Optional[bool] = None
    allow_stream: Optional[bool] = None
    use_proxy: Optional[bool] = None
    play_id: Optional[str] = None
    enabled: Optional[bool] = None
    interval_days: Optional[str] = None
    time_range: Optional[str] = None
    useragent: Optional[str] = None
    client: Optional[str] = None
    client_version: Optional[str] = None
    device: Optional[str] = None
    device_id: Optional[str] = None


class EmbyServerResponse(BaseModel):
    """Response model for Emby server account (never includes password/token)."""

    id: str
    url: str
    username: str
    name: Optional[str] = None
    auth_method: str = "token"
    time: Optional[Union[int, List[int]]] = None
    allow_multiple: Optional[bool] = True
    allow_stream: Optional[bool] = False
    use_proxy: Optional[bool] = True
    play_id: Optional[str] = None
    enabled: Optional[bool] = True
    interval_days: Optional[str] = None
    time_range: Optional[str] = None
    useragent: Optional[str] = None
    client: Optional[str] = None
    client_version: Optional[str] = None
    device: Optional[str] = None
    device_id: Optional[str] = None
    # Status fields (populated from runtime data)
    has_token: bool = False
    is_online: Optional[bool] = None
    last_login_time: Optional[datetime] = None
    last_watch_time: Optional[datetime] = None
    last_watch_status: Optional[str] = None
    next_schedule_time: Optional[datetime] = None
    is_running: bool = False


class EmbyServerToggle(BaseModel):
    enabled: bool


# ============ Action Models ============


class ActionResponse(BaseModel):
    run_id: str
    status: str = "started"
    message: str


class CancelResponse(BaseModel):
    status: str
    message: str


# ============ Schedule Models ============


class ScheduleInfo(BaseModel):
    id: str
    account_spec: str
    interval_days: Optional[str] = None
    time_range: Optional[str] = None
    next_time: Optional[datetime] = None
    is_running: bool = False
    last_status: Optional[str] = None
    enabled: bool = True


class SchedulePreviewRequest(BaseModel):
    interval_days: Optional[str] = None
    time_range: Optional[str] = None


class SchedulePreviewResponse(BaseModel):
    interval_days: str
    time_range: str
    next_time: datetime


# ============ Config Models ============


class ProxyConfigUpdate(BaseModel):
    hostname: Optional[str] = None
    port: Optional[StrictInt] = Field(default=None, gt=0)
    scheme: Optional[str] = Field(default=None, pattern="^(socks5|http)$")


class GlobalConfigUpdate(BaseModel):
    proxy: Optional[ProxyConfigUpdate] = None
    emby_time_range: Optional[str] = None
    emby_interval_days: Optional[str] = None
    emby_concurrency: Optional[StrictInt] = Field(default=None, gt=0)


class GlobalConfigResponse(BaseModel):
    emby_time_range: Optional[str] = None
    emby_interval_days: Optional[str] = None
    emby_concurrency: Optional[int] = None
    proxy_hostname: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_scheme: Optional[str] = None


class NotifierConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    method: Optional[str] = "apprise"
    apprise_uri: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    clear: Optional[bool] = False


class NotifierConfigResponse(BaseModel):
    enabled: bool = False
    method: str = "apprise"
    configured: bool = False
    target_label: Optional[str] = None
    telegram_chat_id: Optional[str] = None


# ============ Status Models ============


class DashboardStatus(BaseModel):
    total_servers: int = 0
    enabled_servers: int = 0
    running_servers: int = 0
    online_servers: int = 0
    last_global_watch_time: Optional[datetime] = None


class HealthStatus(BaseModel):
    status: str
    config_loaded: bool = False
    scheduler_initialized: bool = False
    scheduler_task_running: bool = False
    account_count: int = 0
    schedule_count: int = 0
    config_writable: bool = False
    web_accounts_writable: bool = False
    auth_configured: bool = False
    notifier_configured: bool = False
    notifier_ready: bool = False
    config_path: Optional[str] = None
    web_accounts_path: Optional[str] = None
    latest_run_id: Optional[str] = None
    latest_run_status: Optional[str] = None
    latest_run_status_info: Optional[str] = None
    scheduler_error: Optional[str] = None
    notifier_last_status: Optional[str] = None
    notifier_last_time: Optional[datetime] = None
    notifier_last_error: Optional[str] = None


class RunHistoryItem(BaseModel):
    run_id: str
    description: Optional[str] = None
    status: str = "unknown"
    status_info: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration: Optional[float] = None
    account_spec: Optional[str] = None
    is_running: bool = False
    log_count: int = 0


class RunLogItem(BaseModel):
    level: str
    message: str
    time: datetime


class RunLogResponse(BaseModel):
    run_id: str
    logs: List[RunLogItem] = Field(default_factory=list)


class RunCleanupResponse(BaseModel):
    status: str
    deleted: int = 0


class ConfigExportResponse(BaseModel):
    generated_at: datetime
    config_path: Optional[str] = None
    web_accounts_path: Optional[str] = None
    config_toml: Optional[str] = None
    web_accounts: dict = Field(default_factory=dict)
    redacted: bool = True


class ConfigBackupResponse(BaseModel):
    status: str
    backup_dir: str
    files: List[str] = Field(default_factory=list)


class ConfigBackupItem(BaseModel):
    id: str
    backup_dir: str
    created_at: Optional[datetime] = None
    files: List[str] = Field(default_factory=list)


class ConfigRestoreRequest(BaseModel):
    confirm: bool = False


class ConfigRestoreResponse(BaseModel):
    status: str
    backup_dir: str
    restored_files: List[str] = Field(default_factory=list)
    safety_backup_dir: Optional[str] = None
