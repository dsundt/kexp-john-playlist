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


def test_run_dedupe_same_uri_dup_deletes_then_readds_and_backs_up(fake_session, tmp_path):
    # The playlist holds the SAME uri twice (exact id dup) -> keep 1, so the
    # uri-based removal must DELETE the uri then re-add one kept copy.
    fake_session.queue("get", _dup_playlist_response())
    fake_session.queue("delete", FakeResponse(200, {"snapshot_id": "S2"}))
    fake_session.queue("post", FakeResponse(201, {"snapshot_id": "S3"}))
    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)

    backup_dir = str(tmp_path / "backups")
    removed, report = run_dedupe(
        sc, "tok", "pl", backup_dir, now_str="20260723T103000", dry_run=False
    )

    assert removed == 1
    assert report == []
    # DELETE issued uri-based (no positions) with the earliest-kept snapshot.
    delete_calls = [c for c in fake_session.calls if c["method"] == "delete"]
    assert len(delete_calls) == 1
    body = delete_calls[0]["json"]
    assert body["snapshot_id"] == "S1"
    assert body["tracks"] == [{"uri": "spotify:track:a"}]
    # One kept copy re-added (total_count=2, remove_count=1 -> keep=1).
    post_calls = [c for c in fake_session.calls if c["method"] == "post"]
    assert len(post_calls) == 1
    assert post_calls[0]["json"] == {"uris": ["spotify:track:a"]}
    # Backup written before the delete.
    bpath = os.path.join(backup_dir, "playlist-20260723T103000.json")
    assert os.path.exists(bpath)
    payload = json.load(open(bpath))
    assert payload["count"] == 2 and payload["snapshot_id"] == "S1"


def _distinct_dup_playlist_response():
    """Two DISTINCT uris that safe-key collide (same normalized title/artist) ->
    the removed uri appears once, so it's a pure DELETE with NO re-add."""
    keep = {"uri": "spotify:track:a", "id": "a", "name": "Song",
            "artists": [{"name": "X"}], "album": {"release_date": "2020"},
            "external_ids": {"isrc": "I1"}}
    dup = {"uri": "spotify:track:b", "id": "b", "name": "Song",
           "artists": [{"name": "X"}], "album": {"release_date": "2020"},
           "external_ids": {"isrc": "I2"}}
    return FakeResponse(200, {"snapshot_id": "S1", "items": [{"track": keep}, {"track": dup}]})


def test_run_dedupe_distinct_uri_deletes_no_readd(fake_session, tmp_path):
    fake_session.queue("get", _distinct_dup_playlist_response())
    fake_session.queue("delete", FakeResponse(200, {"snapshot_id": "S2"}))
    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)

    backup_dir = str(tmp_path / "backups")
    removed, report = run_dedupe(
        sc, "tok", "pl", backup_dir, now_str="20260723T103000", dry_run=False
    )

    assert removed == 1
    delete_calls = [c for c in fake_session.calls if c["method"] == "delete"]
    post_calls = [c for c in fake_session.calls if c["method"] == "post"]
    assert len(delete_calls) == 1
    assert delete_calls[0]["json"]["tracks"] == [{"uri": "spotify:track:b"}]
    assert delete_calls[0]["json"]["snapshot_id"] == "S1"
    assert post_calls == []   # distinct uri appears once -> no re-add


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
