from embykeeper.apprise import AppriseStream, get_delivery_status


def test_apprise_stream_ignores_blank_messages(monkeypatch):
    calls = []

    class DummyApprise:
        def add(self, _uri):
            return True

        def notify(self, **kwargs):
            calls.append(kwargs)
            return True

    monkeypatch.setattr("embykeeper.apprise.apprise.Apprise", DummyApprise)

    stream = AppriseStream("mailto://user@example.com")
    stream.write("\n")
    stream.write("   ")

    assert calls == []


def test_apprise_stream_sends_plain_message_without_level_prefix(monkeypatch):
    calls = []

    class DummyApprise:
        def add(self, _uri):
            return True

        def notify(self, **kwargs):
            calls.append(kwargs)
            return True

    monkeypatch.setattr("embykeeper.apprise.apprise.Apprise", DummyApprise)

    stream = AppriseStream("mailto://user@example.com")
    stream.write("plain message")

    assert len(calls) == 1
    assert calls[0]["body"] == "plain message"
    assert get_delivery_status()["status"] == "sent"


def test_apprise_stream_skips_notify_when_uri_is_invalid(monkeypatch):
    class DummyApprise:
        def add(self, _uri):
            return False

        def notify(self, **_kwargs):
            raise AssertionError("notify should not be called for invalid uri")

    monkeypatch.setattr("embykeeper.apprise.apprise.Apprise", DummyApprise)

    stream = AppriseStream("invalid://uri")
    stream.write("plain message")

    assert get_delivery_status()["status"] == "error"
    assert get_delivery_status()["error"] == "InvalidURI"


def test_apprise_stream_skips_notify_when_uri_add_raises(monkeypatch):
    class DummyApprise:
        def add(self, _uri):
            raise RuntimeError("invalid uri")

        def notify(self, **_kwargs):
            raise AssertionError("notify should not be called when uri setup fails")

    monkeypatch.setattr("embykeeper.apprise.apprise.Apprise", DummyApprise)

    stream = AppriseStream("invalid://uri")
    stream.write("plain message")


def test_apprise_stream_ignores_notify_exceptions(monkeypatch):
    class DummyApprise:
        def add(self, _uri):
            return True

        def notify(self, **_kwargs):
            raise RuntimeError("send failed")

    monkeypatch.setattr("embykeeper.apprise.apprise.Apprise", DummyApprise)

    stream = AppriseStream("mailto://user@example.com")
    stream.write("plain message")

    assert get_delivery_status()["status"] == "error"
    assert get_delivery_status()["error"] == "RuntimeError"


def test_apprise_stream_keeps_body_when_markup_is_invalid(monkeypatch):
    calls = []

    class DummyApprise:
        def add(self, _uri):
            return True

        def notify(self, **kwargs):
            calls.append(kwargs)
            return True

    monkeypatch.setattr("embykeeper.apprise.apprise.Apprise", DummyApprise)

    stream = AppriseStream("mailto://user@example.com")
    stream.write("ERROR#[/broken]")

    assert len(calls) == 1
    assert calls[0]["body"] == "[/broken]"
