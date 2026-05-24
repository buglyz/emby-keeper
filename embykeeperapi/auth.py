import hashlib
import os
import time
from datetime import datetime, timedelta

from typing import Dict, List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

ALGORITHM = "HS256"
DEFAULT_EXPIRE_DAYS = 7

security = HTTPBearer()

# Simple rate limiter: track failed login attempts
_failed_attempts: Dict[str, List[float]] = {}
MAX_FAILED_PER_HOUR = 5


def _get_jwt_secret() -> str:
    """Get the JWT signing secret from env or derive from EK_WEBPASS."""
    secret = os.environ.get("EK_SECRET")
    if secret:
        return secret
    webpass = os.environ.get("EK_WEBPASS")
    if webpass:
        return hashlib.sha256(webpass.encode()).hexdigest()
    # Fallback: auto-generated secret stored in basedir (handled by crypto.py)
    return "embykeeper-default-secret-change-me"


JWT_SECRET = _get_jwt_secret()


def create_jwt(subject: str = "admin", expire_days: int = DEFAULT_EXPIRE_DAYS) -> str:
    """Create a JWT token."""
    expire = datetime.utcnow() + timedelta(days=expire_days)
    payload = {"sub": subject, "exp": expire, "iat": datetime.utcnow()}
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def verify_jwt(token: str) -> dict:
    """Verify a JWT token and return its payload."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """FastAPI dependency that validates JWT and returns the subject."""
    payload = verify_jwt(credentials.credentials)
    return payload.get("sub", "admin")


def validate_pre_shared_token(token: str) -> bool:
    """Validate a pre-shared token against EK_TOKEN env var."""
    expected = os.environ.get("EK_TOKEN")
    if not expected:
        return False
    return token == expected


def validate_password(password: str) -> bool:
    """Validate a password against EK_WEBPASS env var."""
    expected = os.environ.get("EK_WEBPASS")
    if not expected:
        return False
    return password == expected


def check_rate_limit(ip: str) -> bool:
    """Check if an IP has exceeded the failed login rate limit."""
    now = time.time()
    attempts = _failed_attempts.get(ip, [])
    # Remove attempts older than 1 hour
    attempts = [t for t in attempts if now - t < 3600]
    _failed_attempts[ip] = attempts
    return len(attempts) < MAX_FAILED_PER_HOUR


def record_failed_attempt(ip: str):
    """Record a failed login attempt for rate limiting."""
    now = time.time()
    if ip not in _failed_attempts:
        _failed_attempts[ip] = []
    _failed_attempts[ip].append(now)