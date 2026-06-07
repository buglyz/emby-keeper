from cryptography.fernet import Fernet

from embykeeperapi.auth import init_jwt_secret_from_basedir
from embykeeperapi.crypto import decrypt_token, encrypt_token, reset_fernet


def test_jwt_secret_file_does_not_break_fernet_key_generation(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    init_jwt_secret_from_basedir(tmp_path)
    reset_fernet()

    encrypted = encrypt_token("emby-token", tmp_path)

    assert (tmp_path / "jwt_secret.key").is_file()
    assert (tmp_path / "secret.key").is_file()
    assert decrypt_token(encrypted, tmp_path) == "emby-token"


def test_legacy_non_fernet_secret_key_is_stably_derived(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    legacy_key = b"a" * 64
    (tmp_path / "secret.key").write_bytes(legacy_key)

    reset_fernet()
    encrypted = encrypt_token("emby-token", tmp_path)

    reset_fernet()
    assert decrypt_token(encrypted, tmp_path) == "emby-token"
    assert (tmp_path / "secret.key").read_bytes() == legacy_key


def test_existing_valid_fernet_secret_key_is_used_as_is(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    key = Fernet.generate_key()
    (tmp_path / "secret.key").write_bytes(key)

    reset_fernet()
    encrypted = encrypt_token("emby-token", tmp_path)

    assert Fernet(key).decrypt(encrypted.encode()).decode() == "emby-token"
