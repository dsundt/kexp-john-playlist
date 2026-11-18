import pytest
import requests

from kexp.http import request_json, MAX_RETRY_AFTER_S
from tests.conftest import FakeResponse


def test_retries_network_then_succeeds(fake_session):
    calls = {"n": 0}

    def flaky(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("x")
        return FakeResponse(200, {"ok": True})

    fake_session.post = flaky
    r = request_json(fake_session, "post", "http://x", sleep=lambda *_: None)
    assert r.json()["ok"] and calls["n"] == 3


def test_4xx_raises(fake_session):
    fake_session.queue("get", FakeResponse(400, text="bad"))
    with pytest.raises(requests.HTTPError):
        request_json(fake_session, "get", "http://x", sleep=lambda *_: None)


def test_429_retries_then_succeeds_and_honors_retry_after(fake_session):
    fake_session.queue(
        "get",
        FakeResponse(429, text="rate limited", headers={"Retry-After": "7"}),
        FakeResponse(200, {"ok": True}),
    )
    sleeps = []
    r = request_json(fake_session, "get", "http://x", sleep=sleeps.append)
    assert r.json()["ok"] is True
    assert sleeps == [7.0]  # honoured Retry-After, not the default backoff


def test_429_without_retry_after_uses_default_backoff(fake_session):
    fake_session.queue(
        "get",
        FakeResponse(429, text="rate limited"),
        FakeResponse(200, {"ok": True}),
    )
    sleeps = []
    r = request_json(fake_session, "get", "http://x", sleep=sleeps.append)
    assert r.json()["ok"] is True
    assert sleeps == [2.0]  # 2 * (attempt 0 + 1)


def test_429_retry_after_is_clamped(fake_session):
    fake_session.queue(
        "get",
        FakeResponse(429, text="rate limited", headers={"Retry-After": "9999"}),
        FakeResponse(200, {"ok": True}),
    )
    sleeps = []
    r = request_json(fake_session, "get", "http://x", sleep=sleeps.append)
    assert r.json()["ok"] is True
    assert sleeps == [float(MAX_RETRY_AFTER_S)]


def test_429_exhausts_retries_then_raises(fake_session):
    for _ in range(3):
        fake_session.queue(
            "get",
            FakeResponse(429, text="rate limited", headers={"Retry-After": "1"}),
        )
    sleeps = []
    with pytest.raises(requests.HTTPError):
        request_json(fake_session, "get", "http://x", sleep=sleeps.append)
    assert len(fake_session.calls) == 3
    assert sleeps == [1.0, 1.0]  # sleep only between attempts, never after the last
