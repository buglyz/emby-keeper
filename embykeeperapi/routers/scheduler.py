from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user
from ..models import ScheduleInfo, DashboardStatus
from ..scheduler_bridge import bridge

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
    account_spec = schedule_id.removeprefix("emby.watch.")

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
