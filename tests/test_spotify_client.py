def _cfg():
    from kexp.config import Config
    return Config.from_env({"SPOTIFY_CLIENT_ID":"i","SPOTIFY_CLIENT_SECRET":"s",
        "SPOTIFY_REFRESH_TOKEN":"r","SPOTIFY_PLAYLIST_ID":"pl"})


def test_fetch_playlist_gets_snapshot_id_from_playlist_object_not_tracks(fake_session):
    """Real Spotify behavior: GET .../tracks paging responses do NOT carry
    snapshot_id (only items+next). snapshot_id lives on the playlist object
    (GET .../playlists/{id}). fetch_playlist must issue that extra GET and
    use ITS snapshot_id -- not whatever (if anything) rode along on a tracks
    page -- and must still return the tracks unchanged."""
    from kexp.spotify_client import SpotifyClient
    from tests.conftest import FakeResponse

    tr = {
        "uri": "spotify:track:a", "id": "a", "name": "Song",
        "artists": [{"name": "X"}], "album": {"release_date": "2020"},
        "external_ids": {"isrc": "I1"},
    }
    # Playlist-object GET: ONLY place a real snapshot_id ever appears.
    fake_session.queue("get", FakeResponse(200, {"snapshot_id": "REAL_SNAPSHOT"}))
    # Tracks paging GET: real shape has NO snapshot_id key at all.
    fake_session.queue("get", FakeResponse(200, {"items": [{"track": tr}], "next": None}))

    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)
    snapshot_id, items = sc.fetch_playlist("tok")

    assert snapshot_id == "REAL_SNAPSHOT"
    assert len(items) == 1
    assert items[0]["uri"] == "spotify:track:a"

    get_calls = [c for c in fake_session.calls if c["method"] == "get"]
    assert len(get_calls) == 2
    # One call must target the playlist object (no /tracks suffix), the
    # other the tracks paging endpoint.
    playlist_obj_calls = [c for c in get_calls if not c["url"].endswith("/tracks")]
    tracks_calls = [c for c in get_calls if c["url"].endswith("/tracks")]
    assert len(playlist_obj_calls) == 1
    assert len(tracks_calls) == 1
    assert "/playlists/pl" in playlist_obj_calls[0]["url"]


def test_remove_duplicate_uris_distinct_deletes_no_readd(fake_session):
    """(a) A uri that appears once (remove all of it) → one DELETE, NO re-add POST."""
    from kexp.spotify_client import SpotifyClient
    from tests.conftest import FakeResponse
    fake_session.queue("delete", FakeResponse(200, {"snapshot_id": "S2"}))
    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)
    sc.remove_duplicate_uris("tok", "S1",
        [{"uri": "u1", "remove_count": 1, "total_count": 1}])
    delete_calls = [c for c in fake_session.calls if c["method"] == "delete"]
    post_calls = [c for c in fake_session.calls if c["method"] == "post"]
    assert len(delete_calls) == 1
    assert delete_calls[0]["json"]["snapshot_id"] == "S1"          # (c) snapshot present
    assert delete_calls[0]["json"]["tracks"] == [{"uri": "u1"}]    # uri-only, no positions
    assert post_calls == []                                        # NO re-add


def test_remove_duplicate_uris_same_uri_dup_deletes_then_readds(fake_session):
    """(b) uri appears twice, remove 1 → DELETE {uri} then POST {uris:[uri]} once."""
    from kexp.spotify_client import SpotifyClient
    from tests.conftest import FakeResponse
    fake_session.queue("delete", FakeResponse(200, {"snapshot_id": "S2"}))
    fake_session.queue("post", FakeResponse(201, {"snapshot_id": "S3"}))
    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)
    sc.remove_duplicate_uris("tok", "S1",
        [{"uri": "u1", "remove_count": 1, "total_count": 2}])
    delete_calls = [c for c in fake_session.calls if c["method"] == "delete"]
    post_calls = [c for c in fake_session.calls if c["method"] == "post"]
    assert len(delete_calls) == 1
    assert delete_calls[0]["json"]["tracks"] == [{"uri": "u1"}]
    assert delete_calls[0]["json"]["snapshot_id"] == "S1"
    assert len(post_calls) == 1                                    # exactly one re-add
    assert post_calls[0]["json"] == {"uris": ["u1"]}              # keep=1 copy re-added


def test_remove_duplicate_uris_batches_deletes_over_100(fake_session):
    """(d) >100 uris → chunked into ≤100-object DELETE bodies, each with snapshot_id."""
    from kexp.spotify_client import SpotifyClient
    from tests.conftest import FakeResponse
    fake_session.queue("delete",
        FakeResponse(200, {"snapshot_id": "S2"}),
        FakeResponse(200, {"snapshot_id": "S3"}))
    sc = SpotifyClient(fake_session, _cfg(), sleep=lambda *_: None)
    removals = [{"uri": f"u{i}", "remove_count": 1, "total_count": 1} for i in range(150)]
    sc.remove_duplicate_uris("tok", "S1", removals)
    delete_calls = [c for c in fake_session.calls if c["method"] == "delete"]
    assert len(delete_calls) == 2                                  # 100 + 50
    assert len(delete_calls[0]["json"]["tracks"]) == 100
    assert len(delete_calls[1]["json"]["tracks"]) == 50
    assert all(c["json"]["snapshot_id"] == "S1" for c in delete_calls)  # (c) every DELETE


def test_replace_with_verify_raises_on_mismatch(fake_session):
    from kexp.spotify_client import SpotifyClient, PlaylistVerifyError
    from kexp.config import Config
    from tests.conftest import FakeResponse
    import pytest
    c=Config.from_env({"SPOTIFY_CLIENT_ID":"i","SPOTIFY_CLIENT_SECRET":"s",
        "SPOTIFY_REFRESH_TOKEN":"r","SPOTIFY_PLAYLIST_ID":"pl"})
    fake_session.queue("put",FakeResponse(200,{"snapshot_id":"S3"}))
    # Re-fetch after the write: playlist-object GET (snapshot_id) then the
    # tracks-paging GET, which returns a SHORT set (only u1) — should raise.
    fake_session.queue("get", FakeResponse(200, {"snapshot_id": "S3"}))
    fake_session.queue("get",FakeResponse(200,{
        "items":[{"track":{"uri":"u1","id":"a","external_ids":{"isrc":"I1"},
                            "name":"Song","artists":[{"name":"X"}],
                            "album":{"release_date":"2001"}}}],
        "next":None,
    }))
    sc=SpotifyClient(fake_session,c,sleep=lambda *_:None)
    with pytest.raises(PlaylistVerifyError):
        sc.replace_with_verify("tok",["u1","u2"],{"u1","u2"})
