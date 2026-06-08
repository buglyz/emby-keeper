from embykeeperapi.models import EmbyServerCreate


def test_server_create_default_time_is_not_shared():
    first = EmbyServerCreate(url="https://example.com", username="alice")
    second = EmbyServerCreate(url="https://example.net", username="bob")

    first.time.append(900)

    assert second.time == [300, 600]
