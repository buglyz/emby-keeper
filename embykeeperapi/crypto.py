import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet


_fernet_instance = None
FERNET_KEY_FILE = "secret.key"


def _derive_fernet_key(secret: bytes) -> bytes:
    """Derive a valid Fernet key from arbitrary secret bytes."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt = b"embykeeper-fernet-salt"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    raw_key = kdf.derive(secret)
    return base64.urlsafe_b64encode(raw_key)


def _write_key(key_file: Path, key: bytes):
    tmp_path = key_file.with_suffix(f"{key_file.suffix}.tmp")
    try:
        tmp_path.write_bytes(key)
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(key_file)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _chmod_key_file(key_file: Path):
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass


def _get_key(basedir: Path) -> bytes:
    """Get or generate the Fernet encryption key."""
    env_secret = os.environ.get("EK_SECRET")
    if env_secret:
        key = env_secret.encode()
        try:
            Fernet(key)
        except Exception:
            key = _derive_fernet_key(env_secret.encode())
        return key
    # Auto-generate and store in basedir
    key_file = basedir / FERNET_KEY_FILE
    if key_file.is_file():
        key = key_file.read_bytes().strip()
        _chmod_key_file(key_file)
        if not key:
            key = Fernet.generate_key()
            _write_key(key_file, key)
            return key
        try:
            Fernet(key)
            return key
        except Exception:
            return _derive_fernet_key(key)
    key = Fernet.generate_key()
    _write_key(key_file, key)
    return key


def get_fernet(basedir: Path) -> Fernet:
    """Get the Fernet instance for encryption/decryption."""
    global _fernet_instance
    basedir = Path(basedir).resolve()
    env_secret = os.environ.get("EK_SECRET")
    cache_key = ("env", env_secret) if env_secret else ("file", str(basedir))
    if _fernet_instance is None or _fernet_instance[0] != cache_key:
        key = _get_key(basedir)
        _fernet_instance = (cache_key, Fernet(key))
    return _fernet_instance[1]


def encrypt_token(plain_token: str, basedir: Path) -> str:
    """Encrypt a token string, returns base64-encoded encrypted string."""
    f = get_fernet(basedir)
    return f.encrypt(plain_token.encode()).decode()


def decrypt_token(encrypted_token: str, basedir: Path) -> str:
    """Decrypt an encrypted token string."""
    f = get_fernet(basedir)
    return f.decrypt(encrypted_token.encode()).decode()


def reset_fernet():
    """Reset the Fernet instance (use after key change)."""
    global _fernet_instance
    _fernet_instance = None
