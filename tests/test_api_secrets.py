import hashlib
import stat

import pytest
from cryptography.fernet import Fernet

import embykeeperapi.auth as auth
from embykeeperapi.auth import init_jwt_secret_from_basedir
from embykeeperapi import crypto
from embykeeperapi.crypto import decrypt_token, encrypt_token, reset_fernet


def test_jwt_secret_file_does_not_break_fernet_key_generation(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    init_jwt_secret_from_basedir(tmp_path)
    reset_fernet()

    encrypted = encrypt_token("emby-token", tmp_path)

    assert (tmp_path / "jwt_secret.key").is_file()
    assert not (tmp_path / "jwt_secret.key.tmp").exists()
    assert not list(tmp_path.glob(".jwt_secret.key.*.tmp"))
    assert (tmp_path / "secret.key").is_file()
    assert not (tmp_path / "secret.key.tmp").exists()
    assert not list(tmp_path.glob(".secret.key.*.tmp"))
    assert stat.S_IMODE((tmp_path / "secret.key").stat().st_mode) == 0o600
    assert decrypt_token(encrypted, tmp_path) == "emby-token"


def test_fernet_key_generation_creates_missing_basedir(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    basedir = tmp_path / "missing" / "secrets"

    reset_fernet()
    encrypted = encrypt_token("emby-token", basedir)

    assert (basedir / "secret.key").is_file()
    assert decrypt_token(encrypted, basedir) == "emby-token"


def test_blank_env_secret_does_not_override_fernet_key_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EK_SECRET", "   ")

    reset_fernet()
    encrypted = encrypt_token("emby-token", tmp_path)

    assert (tmp_path / "secret.key").is_file()
    assert decrypt_token(encrypted, tmp_path) == "emby-token"


def test_env_secret_is_trimmed_for_fernet_key(tmp_path, monkeypatch):
    monkeypatch.setenv("EK_SECRET", " shared-secret ")

    reset_fernet()
    encrypted = encrypt_token("emby-token", tmp_path)

    monkeypatch.setenv("EK_SECRET", "shared-secret")
    reset_fernet()

    assert decrypt_token(encrypted, tmp_path) == "emby-token"


def test_existing_jwt_secret_file_is_owner_only(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    key_file = tmp_path / "jwt_secret.key"
    key_file.write_bytes(b"jwt-secret")
    key_file.chmod(0o644)

    init_jwt_secret_from_basedir(tmp_path)

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert auth.JWT_SECRET == hashlib.sha256(b"jwt-secret").hexdigest()


def test_jwt_secret_generation_creates_missing_basedir(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    basedir = tmp_path / "missing" / "secrets"
    old_secret = auth.JWT_SECRET

    try:
        init_jwt_secret_from_basedir(basedir)

        key_file = basedir / "jwt_secret.key"
        assert key_file.is_file()
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert auth.JWT_SECRET == hashlib.sha256(key_file.read_bytes()).hexdigest()
    finally:
        auth.JWT_SECRET = old_secret


def test_empty_jwt_secret_file_is_replaced(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    key_file = tmp_path / "jwt_secret.key"
    key_file.write_bytes(b"")

    init_jwt_secret_from_basedir(tmp_path)

    key_bytes = key_file.read_bytes()
    assert key_bytes
    assert not (tmp_path / "jwt_secret.key.tmp").exists()
    assert not list(tmp_path.glob(".jwt_secret.key.*.tmp"))
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert auth.JWT_SECRET == hashlib.sha256(key_bytes).hexdigest()
    assert auth.JWT_SECRET != hashlib.sha256(b"").hexdigest()


def test_new_jwt_secret_write_failure_keeps_runtime_secret(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    old_secret = auth.JWT_SECRET

    def fail_write(_path, _key_bytes):
        raise OSError("disk full")

    monkeypatch.setattr(auth, "_write_secret_file_atomic", fail_write)

    try:
        with pytest.raises(OSError):
            init_jwt_secret_from_basedir(tmp_path)

        assert auth.JWT_SECRET == old_secret
    finally:
        auth.JWT_SECRET = old_secret


def test_jwt_secret_rejects_symlink_secret_file(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    outside = tmp_path / "outside-jwt-secret"
    outside.write_bytes(b"outside-secret")
    key_file = tmp_path / "jwt_secret.key"
    try:
        key_file.symlink_to(outside)
    except OSError:
        return
    old_secret = auth.JWT_SECRET

    try:
        with pytest.raises(OSError, match="symlink"):
            init_jwt_secret_from_basedir(tmp_path)

        assert outside.read_bytes() == b"outside-secret"
        assert auth.JWT_SECRET == old_secret
    finally:
        auth.JWT_SECRET = old_secret


def test_jwt_secret_rejects_symlink_legacy_secret_file(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    outside = tmp_path / "outside-legacy-secret"
    outside.write_bytes(b"legacy-secret")
    legacy_key_file = tmp_path / "secret.key"
    try:
        legacy_key_file.symlink_to(outside)
    except OSError:
        return
    old_secret = auth.JWT_SECRET

    try:
        with pytest.raises(OSError, match="symlink"):
            init_jwt_secret_from_basedir(tmp_path)

        assert outside.read_bytes() == b"legacy-secret"
        assert not (tmp_path / "jwt_secret.key").exists()
        assert auth.JWT_SECRET == old_secret
    finally:
        auth.JWT_SECRET = old_secret


def test_jwt_secret_write_cleans_temp_file_on_type_error(tmp_path):
    key_file = tmp_path / "jwt_secret.key"

    with pytest.raises(TypeError):
        auth._write_secret_file_atomic(key_file, "not-bytes")

    assert not key_file.exists()
    assert not list(tmp_path.glob(".jwt_secret.key.*.tmp"))


def test_empty_legacy_secret_key_does_not_create_fixed_jwt_secret(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "secret.key").write_bytes(b"")

    init_jwt_secret_from_basedir(tmp_path)

    key_bytes = (tmp_path / "jwt_secret.key").read_bytes()
    assert key_bytes
    assert auth.JWT_SECRET == hashlib.sha256(key_bytes).hexdigest()
    assert auth.JWT_SECRET != hashlib.sha256(b"").hexdigest()


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


def test_empty_fernet_secret_key_is_replaced(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    key_file = tmp_path / "secret.key"
    key_file.write_bytes(b"")

    reset_fernet()
    encrypted = encrypt_token("emby-token", tmp_path)

    key = key_file.read_bytes()
    assert key
    Fernet(key)
    assert decrypt_token(encrypted, tmp_path) == "emby-token"


def test_existing_valid_fernet_secret_key_is_used_as_is(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    key = Fernet.generate_key()
    key_file = tmp_path / "secret.key"
    key_file.write_bytes(key)
    key_file.chmod(0o644)

    reset_fernet()
    encrypted = encrypt_token("emby-token", tmp_path)

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert Fernet(key).decrypt(encrypted.encode()).decode() == "emby-token"


def test_fernet_key_rejects_symlink_secret_file(tmp_path, monkeypatch):
    for key in ("EK_SECRET", "EK_WEBPASS", "EK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    outside = tmp_path / "outside-fernet-secret"
    outside.write_bytes(Fernet.generate_key())
    key_file = tmp_path / "secret.key"
    try:
        key_file.symlink_to(outside)
    except OSError:
        return

    reset_fernet()
    with pytest.raises(OSError, match="symlink"):
        encrypt_token("emby-token", tmp_path)

    assert outside.is_file()
    assert key_file.is_symlink()


def test_fernet_key_write_cleans_temp_file_on_type_error(tmp_path):
    key_file = tmp_path / "secret.key"

    with pytest.raises(TypeError):
        crypto._write_key(key_file, "not-bytes")

    assert not key_file.exists()
    assert not list(tmp_path.glob(".secret.key.*.tmp"))


def test_fernet_key_write_ignores_chmod_failure(tmp_path, monkeypatch):
    key_file = tmp_path / "secret.key"
    key = Fernet.generate_key()

    def fail_chmod(*_args, **_kwargs):
        raise OSError("chmod unsupported")

    monkeypatch.setattr(crypto.os, "chmod", fail_chmod)

    crypto._write_key(key_file, key)

    assert key_file.read_bytes() == key
    assert not list(tmp_path.glob(".secret.key.*.tmp"))


@pytest.mark.parametrize("plain_token", ["", None, 123, True])
def test_encrypt_token_rejects_invalid_plain_tokens(tmp_path, plain_token):
    with pytest.raises(ValueError):
        encrypt_token(plain_token, tmp_path)


@pytest.mark.parametrize("encrypted_token", ["", None, 123, True])
def test_decrypt_token_rejects_invalid_ciphertexts(tmp_path, encrypted_token):
    with pytest.raises(ValueError):
        decrypt_token(encrypted_token, tmp_path)
