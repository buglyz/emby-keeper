from embykeeper.apprise import AppriseStream


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
