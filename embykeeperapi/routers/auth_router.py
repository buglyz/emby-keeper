from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth import (
    validate_password,
    validate_pre_shared_token,
    create_jwt,
    check_rate_limit,
    clear_failed_attempts,
    record_failed_attempt,
    get_current_user,
    get_client_ip,
    DEFAULT_EXPIRE_DAYS,
    _get_env_secret,
)
from ..models import TokenExchangeRequest, PasswordLoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token-exchange", response_model=LoginResponse)
async def exchange_token(req: TokenExchangeRequest, request: Request):
    """Exchange a pre-shared token (EK_TOKEN) for a JWT."""
    client_ip = get_client_ip(request)

    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
        )

    if not validate_pre_shared_token(req.token):
        record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid pre-shared token",
        )
    clear_failed_attempts(client_ip)
    jwt_token = create_jwt(subject="admin", expire_days=DEFAULT_EXPIRE_DAYS)
    return LoginResponse(
        access_token=jwt_token,
        expires_in=DEFAULT_EXPIRE_DAYS * 86400,
    )


@router.post("/login", response_model=LoginResponse)
async def login_with_password(req: PasswordLoginRequest, request: Request):
    """Exchange a password (EK_WEBPASS) for a JWT. Rate-limited."""
    client_ip = get_client_ip(request)

    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
        )

    if not validate_password(req.password):
        record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    clear_failed_attempts(client_ip)
    jwt_token = create_jwt(subject="admin", expire_days=DEFAULT_EXPIRE_DAYS)
    return LoginResponse(
        access_token=jwt_token,
        expires_in=DEFAULT_EXPIRE_DAYS * 86400,
    )


@router.get("/me")
async def verify_token(user: str = Depends(get_current_user)):
    """Verify current JWT validity."""
    return {"user": user, "valid": True}


@router.get("/methods")
async def get_auth_methods():
    """Return available auth methods based on env config."""
    has_token = bool(_get_env_secret("EK_TOKEN"))
    has_password = bool(_get_env_secret("EK_WEBPASS"))
    return {
        "token": has_token,
        "password": has_password,
    }
