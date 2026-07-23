"""Task 12: end-to-end dry-run — DO_SPOTIFY_ADDS=0 mutates nothing.

Drives kexp.pipeline.main() with a URL-routing fake session and a fixed clock
set past the dedupe gate (weekday 10:30 PT) on a playlist that contains a dup.
Asserts: no playlist mutation (no DELETE, no PUT, no POST to the tracks
endpoint) and the heartbeat is still written.
"""
import json
import os

from datetime import datetime
from zoneinfo import ZoneInfo

from kexp.pipeline import main, PLAYLIST_ITEMS_TPL
from tests.conftest import FakeResponse

PT = ZoneInfo("America/Los_Angeles")

_DUP_TRACK = {
    "uri": "spotify:track:a", "id": "a", "name": "Song",
    "artists": [{"name": "X"}], "album": {"release_date": "2020"},
    "external_ids": {"isrc": "I1"},
}


class RoutingSession:
    """Fake session that answers by URL substring, order-independent."""
    def __init__(self):
        self.calls = []

    def _record(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})

    def post(self, url, **kw):
        self._record("post", url, **kw)
        if "accounts.spotify.com/api/token" in url:
            return FakeResponse(200, {"access_token": "tok"})
        return FakeResponse(201, {})

    def get(self, url, **kw):
        self._record("get", url, **kw)
        if "api.spotify.com/v1/playlists" in url:
            return FakeResponse(200, {"snapshot_id": "S1",
                                      "items": [{"track": _DUP_TRACK}, {"track": _DUP_TRACK}]})
        if "kexp.org" in url:
            return FakeResponse(200, {"results": []})
        return FakeResponse(200, {})

    def put(self, url, **kw):
        self._record("put", url, **kw)
        return FakeResponse(200, {})

    def delete(self, url, **kw):
        self._record("delete", url, **kw)
        return FakeResponse(200, {"snapshot_id": "S2"})


def test_e2e_dry_run_no_mutations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Skip the one-time backfill so the run only exercises live + gates.
    os.makedirs("data", exist_ok=True)
    with open("data/seen.json", "w") as f:
        json.dump({"keys": [], "flags": {"backfill_done_range": True}}, f)

    env = {
        "SPOTIFY_CLIENT_ID": "i", "SPOTIFY_CLIENT_SECRET": "s",
        "SPOTIFY_REFRESH_TOKEN": "r", "SPOTIFY_PLAYLIST_ID": "pl",
        "DO_SPOTIFY_ADDS": "0",   # dry-run
        "DO_EMAIL": "0",          # keep SMTP out of the test entirely
    }
    now = datetime(2026, 7, 23, 10, 30, tzinfo=PT)  # Thursday, past dedupe gate

    session = RoutingSession()
    main(session=session, env=env, now=now)

    playlist_url = PLAYLIST_ITEMS_TPL.format(playlist_id="pl")
    # No mutating playlist calls whatsoever.
    assert not any(c["method"] == "delete" for c in session.calls)
    assert not any(c["method"] == "put" for c in session.calls)
    assert not any(c["method"] == "post" and c["url"] == playlist_url
                   for c in session.calls), "dry-run must not POST adds/reorder to the playlist"

    # Heartbeat still written despite zero mutations.
    assert os.path.exists("data/heartbeat.json")
    hb = json.load(open("data/heartbeat.json"))
    assert "timestamp" in hb and "counts" in hb
