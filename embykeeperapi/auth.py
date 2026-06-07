import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta

from typing import Dict, List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

ALGORITHM = "HS256"
DEFAULT_EXPIRE_DAYS = 7
JWT_SECRET_FILE = "jwt_secret.key"

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
    token = os.environ.get("EK_TOKEN")
    if token:
        return hashlib.sha256(token.encode()).hexdigest()
    return secrets.token_urlsafe(32)


JWT_SECRET = _get_jwt_secret()


def _chmod_secret_file(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_secret_file_atomic(path, key_bytes: bytes):
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        tmp_path.write_bytes(key_bytes)
        _chmod_secret_file(tmp_path)
        tmp_path.replace(path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def init_jwt_secret_from_basedir(basedir):
    """Re-derive JWT secret using persistent key file when no env vars are set."""
    global JWT_SECRET
    if os.environ.get("EK_SECRET") or os.environ.get("EK_WEBPASS") or os.environ.get("EK_TOKEN"):
        return
    from pathlib import Path

    basedir = Path(basedir)
    key_file = basedir / JWT_SECRET_FILE
    if key_file.is_file():
        key_bytes = key_file.read_bytes().strip()
        if not key_bytes:
            key_bytes = secrets.token_bytes(32)
            _write_secret_file_atomic(key_file, key_bytes)
        _chmod_secret_file(key_file)
        JWT_SECRET = hashlib.sha256(key_bytes).hexdigest()
    else:
        legacy_key_file = basedir / "secret.key"
        if legacy_key_file.is_file():
            key_bytes = legacy_key_file.read_bytes().strip()
        else:
            key_bytes = secrets.token_bytes(32)
        _write_secret_file_atomic(key_file, key_bytes)
        JWT_SECRET = hashlib.sha256(key_bytes).hexdigest()


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


def _constant_time_equal(value: str, expected: str) -> bool:
    return hmac.compare_digest(value.encode(), expected.encode())


def validate_pre_shared_token(token: str) -> bool:
    """Validate a pre-shared token against EK_TOKEN env var."""
    expected = os.environ.get("EK_TOKEN")
    if not expected:
        return False
    return _constant_time_equal(token, expected)


def validate_password(password: str) -> bool:
    """Validate a password against EK_WEBPASS env var."""
    expected = os.environ.get("EK_WEBPASS")
    if not expected:
        return False
    return _constant_time_equal(password, expected)


def check_rate_limit(ip: str) -> bool:
    """Check if an IP has exceeded the failed login rate limit."""
    now = time.time()
    # Periodic cleanup: remove stale IPs (no attempts in last hour)
    if len(_failed_attempts) > 100:
        stale = [k for k, v in _failed_attempts.items() if not v or now - v[-1] > 3600]
        for k in stale:
            del _failed_attempts[k]
    attempts = _failed_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < 3600]
    _failed_attempts[ip] = attempts
    return len(attempts) < MAX_FAILED_PER_HOUR


def record_failed_attempt(ip: str):
    """Record a failed login attempt for rate limiting."""
    now = time.time()
    if ip not in _failed_attempts:
        _failed_attempts[ip] = []
    _failed_attempts[ip].append(now)


def clear_failed_attempts(ip: str):
    """Clear failed login attempts after a successful login."""
    _failed_attempts.pop(ip, None)
