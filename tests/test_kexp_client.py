import pytest
import requests


def test_is_john_show_failure_not_cached_and_rechecked(fake_session):
    from kexp.kexp_client import KexpClient

    calls = {"n": 0}

    def flaky_get(url, **kw):
        calls["n"] += 1
        raise requests.ConnectionError("boom")

    fake_session.get = flaky_get
    kc = KexpClient(fake_session)

    assert kc.is_john_show("http://x/shows/1") is False
    assert kc.is_john_show("http://x/shows/1") is False
    # Not memoized as a failure — the client re-checked (hit the network) both times.
    assert calls["n"] == 2


def test_is_john_show_true_result_is_cached(fake_session):
    from kexp.kexp_client import KexpClient
    from tests.conftest import FakeResponse

    fake_session.queue(
        "get",
        FakeResponse(200, {"hosts": [26], "host_names": ["John Richards"]}),
    )
    kc = KexpClient(fake_session)

    assert kc.is_john_show("http://x/shows/1") is True
    # Second call must be served from cache — no new queued response needed.
    assert kc.is_john_show("http://x/shows/1") is True
    assert len(fake_session.calls) == 1


def test_fetch_plays_returns_results(fake_session):
    from kexp.kexp_client import KexpClient
    from tests.conftest import FakeResponse

    fake_session.queue("get", FakeResponse(200, {"results": [{"id": 1}, {"id": 2}]}))
    kc = KexpClient(fake_session)

    plays = kc.fetch_plays("2026-01-01T00:00:00", "2026-01-01T01:00:00")
    assert plays == [{"id": 1}, {"id": 2}]
    call = fake_session.calls[-1]
    assert call["params"]["airdate_after"] == "2026-01-01T00:00:00"
    assert call["params"]["airdate_before"] == "2026-01-01T01:00:00"
    assert call["params"]["limit"] == 200


def test_fetch_plays_retries_transient_5xx_then_succeeds(fake_session, monkeypatch):
    # Regression: a single KEXP 502 Bad Gateway used to crash the whole run
    # (bare session.get + raise_for_status). fetch_plays now routes through
    # request_json, which retries 5xx with backoff.
    import kexp.http as http
    from kexp.kexp_client import KexpClient
    from tests.conftest import FakeResponse

    monkeypatch.setitem(http.request_json.__kwdefaults__, "sleep", lambda *_: None)
    fake_session.queue(
        "get",
        FakeResponse(502, text="Bad Gateway"),
        FakeResponse(502, text="Bad Gateway"),
        FakeResponse(200, {"results": [{"id": 1}]}),
    )
    kc = KexpClient(fake_session)

    plays = kc.fetch_plays("2026-01-01T00:00:00", "2026-01-01T01:00:00")
    assert plays == [{"id": 1}]
    assert len(fake_session.calls) == 3  # two retries, then success


def test_fetch_plays_raises_after_persistent_5xx(fake_session, monkeypatch):
    # A sustained KEXP outage must still surface as an error (not a silent empty
    # list), so the run is visibly retried on the next schedule.
    import kexp.http as http
    from kexp.kexp_client import KexpClient
    from tests.conftest import FakeResponse

    monkeypatch.setitem(http.request_json.__kwdefaults__, "sleep", lambda *_: None)
    fake_session.queue(
        "get",
        FakeResponse(502, text="Bad Gateway"),
        FakeResponse(502, text="Bad Gateway"),
        FakeResponse(502, text="Bad Gateway"),
    )
    kc = KexpClient(fake_session)

    with pytest.raises(requests.HTTPError):
        kc.fetch_plays("2026-01-01T00:00:00", "2026-01-01T01:00:00")
