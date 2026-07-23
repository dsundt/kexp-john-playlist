"""Task 11: pipeline orchestration — run_dedupe + build_membership."""
import json
import os

from kexp.config import Config
from kexp.spotify_client import SpotifyClient, PlaylistVerifyError
from kexp.pipeline import run_dedupe, run_reorder, build_membership
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


class _ReorderClient:
    """Fake SpotifyClient for run_reorder: playlist needs reordering; verify and
    (optionally) restore fail."""
    def __init__(self, *, restore_raises):
        self._restore_raises = restore_raises
        self.reorder_calls = 0

    def fetch_playlist(self, token):
        # Two tracks out of year order so _compute_reorder != current order.
        items = [
            {"uri": "u1", "id": "1", "isrc": "I1", "name": "B",
             "artist": "X", "album_release_date": "2020", "position": 0},
            {"uri": "u2", "id": "2", "isrc": "I2", "name": "A",
             "artist": "X", "album_release_date": "2000", "position": 1},
        ]
        return "S1", items

    def replace_with_verify(self, token, ordered_uris, expected_set):
        raise PlaylistVerifyError("post-replace verify failed")

    def reorder(self, token, ordered_uris):
        self.reorder_calls += 1
        if self._restore_raises:
            raise RuntimeError("restore write failed")


def test_run_reorder_verify_fail_then_restore_fail_reports_critical(tmp_path, monkeypatch):
    import kexp.pipeline as pipeline
    alerts = []
    monkeypatch.setattr(pipeline.alerting, "send_failure_email",
                        lambda config, *, subject, body: alerts.append((subject, body)) or True)

    client = _ReorderClient(restore_raises=True)
    backup_dir = str(tmp_path / "backups")
    changed, reason = run_reorder(
        client, _cfg(), "tok", backup_dir, now_str="20260723T104000", dry_run=False
    )

    # Restore was attempted and itself failed -> must NOT claim "restored".
    assert client.reorder_calls == 1
    assert changed is False
    assert "restore" in reason.lower() and "fail" in reason.lower()
    assert "restored" not in reason.lower().replace("restore failed", "")
    # A CRITICAL alert naming the restore failure + backup path was sent.
    assert len(alerts) == 1
    subject, body = alerts[0]
    assert "restore" in (subject + body).lower() and "fail" in (subject + body).lower()
    assert "playlist-20260723T104000.json" in body


def test_run_reorder_verify_fail_then_restore_ok_reports_restored(tmp_path, monkeypatch):
    import kexp.pipeline as pipeline
    alerts = []
    monkeypatch.setattr(pipeline.alerting, "send_failure_email",
                        lambda config, *, subject, body: alerts.append((subject, body)) or True)

    client = _ReorderClient(restore_raises=False)
    backup_dir = str(tmp_path / "backups")
    changed, reason = run_reorder(
        client, _cfg(), "tok", backup_dir, now_str="20260723T104000", dry_run=False
    )

    assert client.reorder_calls == 1
    assert changed is False
    assert "restored" in reason.lower()
    assert len(alerts) == 1
