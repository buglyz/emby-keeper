from datetime import datetime, timezone
import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus, _running_runs
from embykeeper.schedule import Scheduler
from embykeeper.schema import EmbyConfig

from ..auth import _get_env_secret, get_current_user
from ..models import (
    CancelResponse,
    DashboardStatus,
    HealthStatus,
    RunCleanupResponse,
    RunLogResponse,
    RunHistoryItem,
    ScheduleInfo,
    SchedulePreviewRequest,
    SchedulePreviewResponse,
)
from ..scheduler_bridge import bridge
from ..validation import validate_schedule_fields

router = APIRouter(tags=["scheduler & status"])


def _require_bridge():
    """Ensure the scheduler bridge is initialized before serving runtime data."""
    if bridge.web_accounts is None:
        raise HTTPException(status_code=503, detail="Service initializing, please retry")


def _datetime_sort_key(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _run_account_spec(run: RunContext):
    description = run.description or ""
    for prefix in ("Manual watch: ", "Login test: "):
        if description.startswith(prefix):
            return _remove_prefix(description, prefix)
    return None


def _remove_prefix(value: str, prefix: str) -> str:
    return value[len(prefix) :] if value.startswith(prefix) else value


def _run_to_history_item(run: RunContext) -> RunHistoryItem:
    return RunHistoryItem(
        run_id=run.id,
        description=run.description,
        status=run.status.name.lower(),
        status_info=run.status_info,
        start_time=run.start_time,
        end_time=run.end_time,
        duration=run.duration,
        account_spec=_run_account_spec(run),
        is_running=run.id in _running_runs,
        log_count=len(run.log or []),
    )


def _latest_run():
    try:
        runs = RunContext.list_recent(limit=1)
    except Exception:
        return None
    return runs[0] if runs else None


def _normalize_run_status_filter(status: str):
    if status is None:
        return None
    if not isinstance(status, str):
        raise HTTPException(status_code=400, detail="status must be a string")
    status = status.strip().lower()
    if not status:
        return None
    valid_statuses = {item.name.lower() for item in RunStatus}
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid run status")
    return status


@router.get("/healthz")
async def healthz():
    """Health check endpoint (no auth required)."""
    return {"status": "ok"}


@router.get("/api/schedule", response_model=List[ScheduleInfo])
async def list_schedule(user: str = Depends(get_current_user)):
    """List all scheduled tasks with next-run times."""
    _require_bridge()
    schedules = bridge.get_schedule_info()
    return [
        ScheduleInfo(
            id=s["id"],
            account_spec=s["account_spec"],
            interval_days=s.get("interval_days"),
            time_range=s.get("time_range"),
            next_time=s.get("next_time"),
            is_running=s.get("is_running", False),
            last_status=None,
            enabled=s.get("enabled", True),
        )
        for s in schedules
    ]


@router.post("/api/schedule/{schedule_id:path}/run-now")
async def run_now(schedule_id: str, user: str = Depends(get_current_user)):
    """Force immediate execution of a scheduled task."""
    _require_bridge()
    account_spec = _remove_prefix(schedule_id, "emby.watch.")

    if account_spec in {"global", "unified"}:
        result = await bridge.trigger_watch_many(unified_only=True)
    else:
        result = await bridge.trigger_watch(account_spec)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {
        "run_id": result.get("run_id", ""),
        "status": result.get("status", "started"),
        "message": result.get("message", ""),
    }


@router.get("/api/status", response_model=DashboardStatus)
async def get_dashboard_status(user: str = Depends(get_current_user)):
    """Get dashboard overview status."""
    _require_bridge()

    accounts = bridge.web_accounts.get_all()
    total = len(accounts)
    enabled = sum(1 for a in accounts.values() if a.get("enabled", True))
    running = 0
    online = 0
    last_global_watch_time = None
    last_global_watch_key = None
    for aid, a in accounts.items():
        st = bridge.get_account_status(aid)
        if st.get("is_running", False):
            running += 1
        if st.get("is_online", False):
            online += 1
        last_watch_time = st.get("last_watch_time")
        watch_time_key = _datetime_sort_key(last_watch_time)
        if watch_time_key is not None and (
            last_global_watch_key is None or watch_time_key > last_global_watch_key
        ):
            last_global_watch_time = last_watch_time
            last_global_watch_key = watch_time_key

    return DashboardStatus(
        total_servers=total,
        enabled_servers=enabled,
        running_servers=running,
        online_servers=online,
        last_global_watch_time=last_global_watch_time,
    )


@router.get("/api/status/health", response_model=HealthStatus)
async def get_health_status(user: str = Depends(get_current_user)):
    """Get operational health details for the Web UI."""
    accounts = bridge.web_accounts.get_all() if bridge.web_accounts else {}
    scheduler_error = None
    try:
        schedules = bridge.get_schedule_info()
    except Exception as e:
        schedules = []
        scheduler_error = type(e).__name__
    config_file = Path(config._conf_file) if config._conf_file else Path(config.basedir) / "config.toml"
    web_accounts_basedir = getattr(bridge.web_accounts, "basedir", None)
    web_accounts_file = (
        Path(web_accounts_basedir) / "web_accounts.json"
        if web_accounts_basedir
        else Path(config.basedir) / "web_accounts.json"
    )
    writable_target = config_file if config_file.exists() else config_file.parent
    notifier = config._cache.notifier if config._cache and config._cache.notifier else None
    scheduler_task = getattr(bridge, "_scheduler_task", None)
    latest_run = _latest_run()

    config_writable = writable_target.exists() and os.access(writable_target, os.W_OK)
    web_accounts_target = web_accounts_file if web_accounts_file.exists() else web_accounts_file.parent
    web_accounts_writable = web_accounts_target.exists() and os.access(web_accounts_target, os.W_OK)
    notifier_ready = False
    notifier_delivery = {}
    if notifier and notifier.enabled and notifier.apprise_uri:
        try:
            from embykeeper import notify
            from embykeeper.apprise import get_delivery_status

            notifier_ready = bool(
                getattr(notify.stream_log, "ready", False) or getattr(notify.stream_msg, "ready", False)
            )
            notifier_delivery = get_delivery_status()
        except Exception:
            notifier_ready = False

    healthy = bool(config._cache and bridge.web_accounts is not None and scheduler_error is None)
    return HealthStatus(
        status="ok" if healthy else "degraded",
        config_loaded=bool(config._cache),
        scheduler_initialized=bridge.web_accounts is not None and bridge.emby_manager is not None,
        scheduler_task_running=bool(scheduler_task and not scheduler_task.done()),
        account_count=len(accounts),
        schedule_count=len(schedules),
        config_writable=config_writable,
        web_accounts_writable=web_accounts_writable,
        auth_configured=bool(_get_env_secret("EK_TOKEN") or _get_env_secret("EK_WEBPASS")),
        notifier_configured=bool(notifier and notifier.enabled and notifier.apprise_uri),
        notifier_ready=notifier_ready,
        config_path=str(config_file),
        web_accounts_path=str(web_accounts_file),
        latest_run_id=latest_run.id if latest_run else None,
        latest_run_status=latest_run.status.name.lower() if latest_run else None,
        latest_run_status_info=latest_run.status_info if latest_run else None,
        scheduler_error=scheduler_error,
        notifier_last_status=notifier_delivery.get("status"),
        notifier_last_time=notifier_delivery.get("time"),
        notifier_last_error=notifier_delivery.get("error"),
    )


@router.post("/api/schedule/preview", response_model=SchedulePreviewResponse)
async def preview_schedule(req: SchedulePreviewRequest, user: str = Depends(get_current_user)):
    """Preview the next schedule time for interval/time-range input."""
    emby_config = config._cache.emby if config._cache and config._cache.emby else EmbyConfig()
    interval_days = req.interval_days if req.interval_days is not None else emby_config.interval_days
    time_range = req.time_range if req.time_range is not None else emby_config.time_range
    validate_schedule_fields(interval_days, time_range, use_defaults=False)

    scheduler = Scheduler.from_str(
        func=lambda _ctx: None,
        interval_days=interval_days,
        time_range=time_range,
        description="Schedule preview",
    )
    return SchedulePreviewResponse(
        interval_days=str(interval_days).strip(),
        time_range=str(time_range).strip(),
        next_time=scheduler.next_time,
    )


@router.post("/api/schedule/{schedule_id:path}/cancel", response_model=CancelResponse)
async def cancel_schedule_run(schedule_id: str, user: str = Depends(get_current_user)):
    """Cancel a currently running scheduled/manual watch task."""
    _require_bridge()
    account_spec = _remove_prefix(schedule_id, "emby.watch.")
    if not bridge.cancel_account_task(account_spec):
        raise HTTPException(status_code=404, detail="No running task found")
    return CancelResponse(status="cancelled", message="Task cancellation requested")


@router.get("/api/runs", response_model=List[RunHistoryItem])
async def list_runs(
    limit: int = 50,
    offset: int = 0,
    status: str = None,
    user: str = Depends(get_current_user),
):
    """List recent run records."""
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    status = _normalize_run_status_filter(status)
    return [
        _run_to_history_item(run)
        for run in RunContext.list_recent(limit=limit, offset=offset, status=status)
    ]


@router.delete("/api/runs", response_model=RunCleanupResponse)
async def cleanup_runs(days: int = 30, user: str = Depends(get_current_user)):
    """Delete cached run records older than the requested number of days."""
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        raise HTTPException(status_code=400, detail="days must be a positive integer")
    return RunCleanupResponse(status="deleted", deleted=RunContext.cleanup_older_than(days))


@router.get("/api/runs/{run_id}", response_model=RunHistoryItem)
async def get_run(run_id: str, user: str = Depends(get_current_user)):
    """Get a single run record."""
    run = RunContext.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_history_item(run)


@router.get("/api/runs/{run_id}/logs", response_model=RunLogResponse)
async def get_run_logs(run_id: str, user: str = Depends(get_current_user)):
    """Get log records captured for a run."""
    run = RunContext.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunLogResponse(
        run_id=run.id,
        logs=[log.model_dump() if hasattr(log, "model_dump") else log for log in (run.log or [])],
    )
