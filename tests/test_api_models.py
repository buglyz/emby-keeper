import pytest
from pydantic import ValidationError

from embykeeperapi.models import (
    ConfigRestoreRequest,
    EmbyServerCreate,
    EmbyServerToggle,
    EmbyServerUpdate,
    NotifierConfigUpdate,
)


def test_server_create_default_time_is_not_shared():
    first = EmbyServerCreate(url="https://example.com", username="alice")
    second = EmbyServerCreate(url="https://example.net", username="bob")

    first.time.append(900)

    assert second.time == [300, 600]


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (
            EmbyServerCreate,
            {
                "url": "https://example.com",
                "username": "alice",
                "allow_stream": "false",
            },
        ),
        (EmbyServerUpdate, {"enabled": "false"}),
        (EmbyServerToggle, {"enabled": "false"}),
        (NotifierConfigUpdate, {"enabled": "true"}),
        (NotifierConfigUpdate, {"clear": "true"}),
        (ConfigRestoreRequest, {"confirm": "true"}),
    ],
)
def test_api_models_reject_string_booleans(model_cls, kwargs):
    with pytest.raises(ValidationError):
        model_cls(**kwargs)
