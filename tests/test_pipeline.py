"""Task 11: pipeline orchestration — run_dedupe + build_membership."""
import json
import os

from kexp.config import Config
from kexp.spotify_client import SpotifyClient
from kexp.pipeline import run_dedupe, build_membership
from tests.conftest import FakeResponse


def _cfg():
    return Config.from_env({
        "SPOTIFY_CLIENT_ID": "i", "SPOTIFY_CLIENT_SECRET": "s",
        "SPOTIFY_REFRESH_TOKEN": "r", "SPOTIFY_PLAYLIST_ID": "pl",
    })


def _dup_playlist_response():
    tr = {
        "uri": "spotify:track:a", "id": "a", "name": "Song",
        "artists": [{"name": "X"}], "album": {"release_date": "2020"},
        "external_ids": {"isrc": "I1"},
    }
    return FakeResponse(200, {"snapshot_id": "S1", "items": [{"track": tr}, {"track": tr}]})


def test_run_dedupe_removes_and_backs_up(fake_session, tmp_path):
    fake_session.queue("get", _dup_playlist_response())
    fake_session.queue("delete", FakeResponse(200, {"snapshot_id": "S2"}))
    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)

    backup_dir = str(tmp_path / "backups")
    removed, report = run_dedupe(
        sc, "tok", "pl", backup_dir, now_str="20260723T103000", dry_run=False
    )

    assert removed == 1
    assert report == []
    # DELETE issued with the earliest-kept snapshot + the dup position.
    delete_calls = [c for c in fake_session.calls if c["method"] == "delete"]
    assert len(delete_calls) == 1
    body = delete_calls[0]["json"]
    assert body["snapshot_id"] == "S1"
    assert {"uri": "spotify:track:a", "positions": [1]} in body["tracks"]
    # Backup written before the delete.
    bpath = os.path.join(backup_dir, "playlist-20260723T103000.json")
    assert os.path.exists(bpath)
    payload = json.load(open(bpath))
    assert payload["count"] == 2 and payload["snapshot_id"] == "S1"


def test_run_dedupe_dry_run_mutates_nothing(fake_session, tmp_path):
    fake_session.queue("get", _dup_playlist_response())
    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)

    backup_dir = str(tmp_path / "backups")
    removed, report = run_dedupe(
        sc, "tok", "pl", backup_dir, now_str="20260723T103000", dry_run=True
    )

    # It still reports what WOULD be removed...
    assert removed == 1
    # ...but performs no DELETE and writes no backup.
    assert not any(c["method"] == "delete" for c in fake_session.calls)
    assert not os.path.exists(os.path.join(backup_dir, "playlist-20260723T103000.json"))


def test_build_membership():
    items = [
        {"uri": "u0", "id": "a", "isrc": "I1", "artist": "The X", "name": "Song!"},
        {"uri": "u1", "id": "b", "isrc": None, "artist": "Y", "name": "Other"},
    ]
    ids, isrcs, safe_keys = build_membership(items)
    assert ids == {"a", "b"}
    assert isrcs == {"I1"}
    assert ("the x", "song") in safe_keys
    assert ("y", "other") in safe_keys
