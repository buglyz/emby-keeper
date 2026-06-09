import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from ipaddress import ip_address, ip_network
from pathlib import Path
from tempfile import NamedTemporaryFile

from typing import Dict, List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

ALGORITHM = "HS256"
DEFAULT_EXPIRE_DAYS = 7
JWT_SECRET_FILE = "jwt_secret.key"
TRUST_PROXY_HEADERS_ENV = "EK_TRUST_PROXY"
TRUSTED_PROXIES_ENV = "EK_TRUSTED_PROXIES"
DEFAULT_TRUSTED_PROXY_NETWORKS = (
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
)

security = HTTPBearer(auto_error=False)

# Simple rate limiter: track failed login attempts
_failed_attempts: Dict[str, List[float]] = {}
MAX_FAILED_PER_HOUR = 5
RATE_LIMIT_WINDOW_SECONDS = 3600
MAX_STORED_FAILED_ATTEMPTS = MAX_FAILED_PER_HOUR
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def _get_env_secret(name: str):
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _get_env_bool(name: str) -> bool:
    value = _get_env_secret(name)
    return bool(value and value.casefold() in TRUTHY_ENV_VALUES)


def _trusted_proxy_networks():
    raw = os.environ.get(TRUSTED_PROXIES_ENV)
    networks = list(DEFAULT_TRUSTED_PROXY_NETWORKS)
    if not isinstance(raw, str) or not raw.strip():
        return networks
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _valid_ip(value: str):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def _trust_proxy_headers_for_host(host: str) -> bool:
    if _get_env_bool(TRUST_PROXY_HEADERS_ENV):
        return True
    try:
        client_ip = ip_address(host)
    except ValueError:
        return False
    return any(client_ip in network for network in _trusted_proxy_networks())


def get_client_ip(request) -> str:
    """Resolve the client IP used for auth rate limiting."""
    direct_ip = request.client.host if getattr(request, "client", None) else "unknown"
    if not _trust_proxy_headers_for_host(direct_ip):
        return direct_ip

    headers = getattr(request, "headers", {}) or {}
    forwarded_for = headers.get("x-forwarded-for")
    if forwarded_for:
        for candidate in forwarded_for.split(","):
            client_ip = _valid_ip(candidate)
            if client_ip:
                return client_ip

    real_ip = _valid_ip(headers.get("x-real-ip"))
    return real_ip or direct_ip


def _get_jwt_secret() -> str:
    """Get the JWT signing secret from env or derive from EK_WEBPASS."""
    secret = _get_env_secret("EK_SECRET")
    if secret:
        return secret
    webpass = _get_env_secret("EK_WEBPASS")
    if webpass:
        return hashlib.sha256(webpass.encode()).hexdigest()
    token = _get_env_secret("EK_TOKEN")
    if token:
        return hashlib.sha256(token.encode()).hexdigest()
    return secrets.token_urlsafe(32)


JWT_SECRET = _get_jwt_secret()


def _chmod_secret_file(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _reject_symlink_secret_file(path: Path):
    if path.is_symlink():
        raise OSError(f"Secret file must not be a symlink: {path.name}")


def _write_secret_file_atomic(path, key_bytes: bytes):
    tmp_path = None
    try:
        with NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(key_bytes)
        _chmod_secret_file(tmp_path)
        tmp_path.replace(path)
    except Exception:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def init_jwt_secret_from_basedir(basedir):
    """Re-derive JWT secret using persistent key file when no env vars are set."""
    global JWT_SECRET
    if _get_env_secret("EK_SECRET") or _get_env_secret("EK_WEBPASS") or _get_env_secret("EK_TOKEN"):
        return

    basedir = Path(basedir)
    basedir.mkdir(parents=True, exist_ok=True)
    key_file = basedir / JWT_SECRET_FILE
    _reject_symlink_secret_file(key_file)
    if key_file.is_file():
        key_bytes = key_file.read_bytes().strip()
        if not key_bytes:
            key_bytes = secrets.token_bytes(32)
            _write_secret_file_atomic(key_file, key_bytes)
        _chmod_secret_file(key_file)
        JWT_SECRET = hashlib.sha256(key_bytes).hexdigest()
    else:
        legacy_key_file = basedir / "secret.key"
        _reject_symlink_secret_file(legacy_key_file)
        if legacy_key_file.is_file():
            key_bytes = legacy_key_file.read_bytes().strip()
        else:
            key_bytes = secrets.token_bytes(32)
        if not key_bytes:
            key_bytes = secrets.token_bytes(32)
        _write_secret_file_atomic(key_file, key_bytes)
        JWT_SECRET = hashlib.sha256(key_bytes).hexdigest()


def create_jwt(subject: str = "admin", expire_days: int = DEFAULT_EXPIRE_DAYS) -> str:
    """Create a JWT token."""
    if not isinstance(subject, str) or not subject:
        raise ValueError("JWT subject must be a non-empty string")
    if not isinstance(expire_days, int) or isinstance(expire_days, bool) or expire_days <= 0:
        raise ValueError("JWT expiration must be a positive integer day count")
    expire = datetime.utcnow() + timedelta(days=expire_days)
    payload = {"sub": subject, "exp": expire, "iat": datetime.utcnow()}
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def verify_jwt(token: str) -> dict:
    """Verify a JWT token and return its payload."""
    if not isinstance(token, str) or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise JWTError("Invalid subject")
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """FastAPI dependency that validates JWT and returns the subject."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    payload = verify_jwt(credentials.credentials)
    return payload["sub"]


def _constant_time_equal(value: str, expected: str) -> bool:
    return hmac.compare_digest(value.encode(), expected.encode())


def validate_pre_shared_token(token: str) -> bool:
    """Validate a pre-shared token against EK_TOKEN env var."""
    expected = _get_env_secret("EK_TOKEN")
    if not expected or not isinstance(token, str):
        return False
    token = token.strip()
    if not token:
        return False
    return _constant_time_equal(token, expected)


def validate_password(password: str) -> bool:
    """Validate a password against EK_WEBPASS env var."""
    expected = _get_env_secret("EK_WEBPASS")
    if not expected or not isinstance(password, str):
        return False
    password = password.strip()
    if not password:
        return False
    return _constant_time_equal(password, expected)


def check_rate_limit(ip: str) -> bool:
    """Check if an IP has exceeded the failed login rate limit."""
    now = time.time()
    # Periodic cleanup: remove stale IPs (no attempts in last hour)
    if len(_failed_attempts) > 100:
        stale = [k for k, v in _failed_attempts.items() if not v or now - v[-1] > RATE_LIMIT_WINDOW_SECONDS]
        for k in stale:
            del _failed_attempts[k]
    attempts = _failed_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if attempts:
        _failed_attempts[ip] = attempts
    else:
        _failed_attempts.pop(ip, None)
    return len(attempts) < MAX_FAILED_PER_HOUR


def record_failed_attempt(ip: str):
    """Record a failed login attempt for rate limiting."""
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    attempts.append(now)
    _failed_attempts[ip] = attempts[-MAX_STORED_FAILED_ATTEMPTS:]


def clear_failed_attempts(ip: str):
    """Clear failed login attempts after a successful login."""
    _failed_attempts.pop(ip, None)
