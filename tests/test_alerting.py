import json
from pathlib import Path

from kexp.config import Config
from kexp.alerting import write_heartbeat, send_failure_email


def _config(**overrides):
    base = {
        "SPOTIFY_CLIENT_ID": "i",
        "SPOTIFY_CLIENT_SECRET": "s",
        "SPOTIFY_REFRESH_TOKEN": "r",
        "SPOTIFY_PLAYLIST_ID": "p",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USERNAME": "u",
        "SMTP_PASSWORD": "pw",
        "MAIL_FROM": "from@example.com",
        "MAIL_TO": "to@example.com",
    }
    base.update(overrides)
    return Config.from_env(base)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_send_failure_email_sends_via_injected_smtp_factory():
    FakeSMTP.sent = []
    cfg = _config()
    ok = send_failure_email(cfg, subject="Boom", body="It broke", smtp_factory=FakeSMTP)
    assert ok is True
    assert len(FakeSMTP.sent) == 1
    assert FakeSMTP.sent[0]["Subject"] == "Boom"
    assert FakeSMTP.sent[0]["To"] == "to@example.com"


def test_send_failure_email_false_when_smtp_config_missing():
    FakeSMTP.sent = []
    cfg = _config(SMTP_HOST="", SMTP_USERNAME="", SMTP_PASSWORD="")
    ok = send_failure_email(cfg, subject="Boom", body="It broke", smtp_factory=FakeSMTP)
    assert ok is False
    assert FakeSMTP.sent == []


def test_write_heartbeat_writes_timestamp_and_counts(tmp_path):
    path = str(tmp_path / "heartbeat.json")
    write_heartbeat(path, now_str="20260723-101500", counts={"added": 3, "removed": 1})
    data = json.loads(Path(path).read_text())
    assert data["timestamp"] == "20260723-101500"
    assert data["counts"] == {"added": 3, "removed": 1}
