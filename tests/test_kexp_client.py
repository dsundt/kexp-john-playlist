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
