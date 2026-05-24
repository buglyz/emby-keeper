import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet


_fernet_instance = None


def _get_key(basedir: Path) -> bytes:
    """Get or generate the Fernet encryption key."""
    env_secret = os.environ.get("EK_SECRET")
    if env_secret:
        # EK_SECRET must be a base64url-encoded 32-byte key
        key = env_secret.encode()
        # Validate it's a proper Fernet key
        try:
            Fernet(key)
        except Exception:
            # If invalid, derive a valid key from the secret string
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            salt = b"embykeeper-fernet-salt"
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=480000,
            )
            raw_key = kdf.derive(env_secret.encode())
            key = base64.urlsafe_b64encode(raw_key)
        return key
    # Auto-generate and store in basedir
    key_file = basedir / "secret.key"
    if key_file.is_file():
        return key_file.read_bytes().strip()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    return key


def get_fernet(basedir: Path) -> Fernet:
    """Get the Fernet instance for encryption/decryption."""
    global _fernet_instance
    if _fernet_instance is None:
        key = _get_key(basedir)
        _fernet_instance = Fernet(key)
    return _fernet_instance


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