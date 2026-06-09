import asyncio
import hashlib
import re
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.schema import TelegramAccount
from embykeeper.utils import format_exception_summary

from ..auth import get_current_user
from ..models import (
    ActionResponse,
    CancelResponse,
    RegistrarQuickRunRequest,
    RegistrarTelegramAccountResponse,
)

router = APIRouter(prefix="/api/registrar", tags=["registrar"])
logger = logger.bind(scheme="embykeeperapi")

_running_registrar_tasks = {}
BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def _get_register_manager_class():
    try:
        from embykeeper.telegram.registrar_main import RegisterManager
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail="Telegram registrar dependencies are not installed; install the telegram extra to use quick registration",
        ) from e
    return RegisterManager


def _require_config():
    if not config._cache:
        raise HTTPException(status_code=503, detail="Config not loaded")


def _telegram_account_id(account: TelegramAccount) -> str:
    raw = account.get_config_key()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _telegram_accounts() -> List[TelegramAccount]:
    _require_config()
    telegram = config._cache.telegram
    return list(telegram.account or []) if telegram else []


def _normalize_text(field: str, value: str, *, max_length: int = 128, allow_whitespace: bool = True) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    if not allow_whitespace and re.search(r"\s", value):
        raise HTTPException(status_code=400, detail=f"{field} must not contain whitespace")
    return value


def _normalize_bot_username(value: str) -> str:
    value = _normalize_text("bot_username", value, max_length=128, allow_whitespace=False)
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].strip().strip("/").lstrip("@")
    if not BOT_USERNAME_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="bot_username is invalid")
    return value


def _normalize_registration_value(field: str, value: str) -> str:
    return _normalize_text(field, value, max_length=128, allow_whitespace=False)


def _select_accounts(account_ids: List[str] = None) -> List[TelegramAccount]:
    accounts = _telegram_accounts()
    if not account_ids:
        return [account for account in accounts if account.enabled]

    id_map = {_telegram_account_id(account): account for account in accounts}
    selected = []
    missing = []
    seen = set()
    for account_id in account_ids:
        if account_id in seen:
            continue
        seen.add(account_id)
        account = id_map.get(account_id)
        if not account:
            missing.append(account_id)
        else:
            selected.append(account)
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown Telegram account: {', '.join(missing)}")
    disabled = [TelegramAccount.get_phone_masked(account.phone) for account in selected if not account.enabled]
    if disabled:
        raise HTTPException(status_code=400, detail=f"Disabled Telegram account selected: {', '.join(disabled)}")
    return selected


def _task_done(run_id: str):
    def cleanup(task: asyncio.Task):
        _running_registrar_tasks.pop(run_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Registrar run {run_id} exited unexpectedly: {format_exception_summary(e)}")

    return cleanup


async def shutdown_registrar_tasks():
    tasks = list(_running_registrar_tasks.values())
    _running_registrar_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Registrar task failed during shutdown: {format_exception_summary(e)}")


async def _run_quick_register(
    ctx: RunContext,
    *,
    bot_username: str,
    accounts: List[TelegramAccount],
    username: str,
    password: str,
    interval_seconds: int,
    timeout_minutes: int,
    register_manager_cls=None,
):
    ctx.start(RunStatus.RUNNING)
    log = ctx.bind_logger(logger)
    manager_cls = register_manager_cls or _get_register_manager_class()
    manager = manager_cls(register_callbacks=False)
    try:
        results = await asyncio.wait_for(
            manager.run_single_bot(
                bot_username,
                accounts=accounts,
                username=username,
                password=password,
                interval_seconds=interval_seconds,
                ctx=ctx,
            ),
            timeout=timeout_minutes * 60,
        )
    except asyncio.CancelledError:
        ctx.finish(RunStatus.CANCELLED, "抢注任务已取消")
        raise
    except asyncio.TimeoutError:
        ctx.finish(RunStatus.FAIL, f"抢注超过 {timeout_minutes} 分钟仍未成功")
        return
    except Exception as e:
        summary = format_exception_summary(e)
        log.warning(f"抢注任务异常退出: {summary}")
        ctx.finish(RunStatus.ERROR, f"抢注异常: {summary}")
        if not config.nofail:
            raise
        return
    finally:
        shutdown = getattr(manager, "shutdown", None)
        if shutdown:
            try:
                await shutdown()
            except Exception as e:
                logger.warning(
                    f"Failed to shutdown quick registrar manager: {format_exception_summary(e)}"
                )

    success_count = sum(1 for result in results if result)
    total = len(results)
    if success_count:
        ctx.finish(RunStatus.SUCCESS, f"{success_count}/{total} 个 Telegram 账号抢注成功")
    else:
        ctx.finish(RunStatus.FAIL, "抢注未成功")


@router.get("/accounts", response_model=List[RegistrarTelegramAccountResponse])
async def list_telegram_accounts(user: str = Depends(get_current_user)):
    return [
        RegistrarTelegramAccountResponse(
            id=_telegram_account_id(account),
            phone_masked=TelegramAccount.get_phone_masked(account.phone),
            enabled=bool(account.enabled),
            registrar=bool(account.registrar),
        )
        for account in _telegram_accounts()
    ]


@router.post("/quick-run", response_model=ActionResponse)
async def quick_register(req: RegistrarQuickRunRequest, user: str = Depends(get_current_user)):
    bot_username = _normalize_bot_username(req.bot_username)
    username = _normalize_registration_value("username", req.username)
    password = _normalize_registration_value("password", req.password)
    interval_seconds = req.interval_seconds if req.interval_seconds is not None else 1
    timeout_minutes = req.timeout_minutes if req.timeout_minutes is not None else 30
    accounts = _select_accounts(req.telegram_account_ids)
    if not accounts:
        raise HTTPException(status_code=400, detail="No enabled Telegram account selected")
    register_manager_cls = _get_register_manager_class()

    account_labels = ", ".join(TelegramAccount.get_phone_masked(account.phone) for account in accounts)
    ctx = RunContext.prepare(
        description=f"一键抢注 @{bot_username}: {username}",
    )
    task = asyncio.create_task(
        _run_quick_register(
            ctx,
            bot_username=bot_username,
            accounts=accounts,
            username=username,
            password=password,
            interval_seconds=interval_seconds,
            timeout_minutes=timeout_minutes,
            register_manager_cls=register_manager_cls,
        )
    )
    ctx._cancel = task.cancel
    _running_registrar_tasks[ctx.id] = task
    task.add_done_callback(_task_done(ctx.id))
    return ActionResponse(
        run_id=ctx.id,
        status="started",
        message=f"已启动 @{bot_username} 抢注任务，Telegram 账号: {account_labels}，最长运行 {timeout_minutes} 分钟",
    )


@router.post("/runs/{run_id}/cancel", response_model=CancelResponse)
async def cancel_quick_register(run_id: str, user: str = Depends(get_current_user)):
    task = _running_registrar_tasks.get(run_id)
    run = RunContext.get(run_id)
    if not task and not (run and run._cancel):
        raise HTTPException(status_code=404, detail="No running registrar task found")
    if task:
        task.cancel()
    elif run:
        run.cancel_tree()
    return CancelResponse(status="cancelled", message="Registrar task cancellation requested")
