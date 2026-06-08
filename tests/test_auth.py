import time

import embykeeperapi.auth as auth


def test_rate_limit_does_not_store_empty_attempts():
    auth._failed_attempts.clear()

    assert auth.check_rate_limit("203.0.113.1") is True

    assert "203.0.113.1" not in auth._failed_attempts


def test_rate_limit_removes_stale_attempts():
    auth._failed_attempts.clear()
    auth._failed_attempts["203.0.113.1"] = [time.time() - 3700]

    assert auth.check_rate_limit("203.0.113.1") is True

    assert "203.0.113.1" not in auth._failed_attempts


def test_pre_shared_token_validation_rejects_non_string(monkeypatch):
    monkeypatch.setenv("EK_TOKEN", "token-1")

    assert auth.validate_pre_shared_token(None) is False
    assert auth.validate_pre_shared_token(123) is False


def test_password_validation_rejects_non_string(monkeypatch):
    monkeypatch.setenv("EK_WEBPASS", "secret")

    assert auth.validate_password(None) is False
    assert auth.validate_password(123) is False
