def test_remove_positions_batches_with_snapshot(fake_session):
    from kexp.spotify_client import SpotifyClient
    from kexp.config import Config
    from tests.conftest import FakeResponse
    c=Config.from_env({"SPOTIFY_CLIENT_ID":"i","SPOTIFY_CLIENT_SECRET":"s",
        "SPOTIFY_REFRESH_TOKEN":"r","SPOTIFY_PLAYLIST_ID":"pl"})
    fake_session.queue("delete",FakeResponse(200,{"snapshot_id":"S2"}))
    sc=SpotifyClient(fake_session,c,sleep=lambda *_:None)
    sc.remove_positions("tok","S1",[("u1",[1]),("u2",[3,4])])
    call=fake_session.calls[-1]
    assert call["method"]=="delete"
    body=call["json"]
    assert body["snapshot_id"]=="S1"
    assert {"uri":"u1","positions":[1]} in body["tracks"]


def test_replace_with_verify_raises_on_mismatch(fake_session):
    from kexp.spotify_client import SpotifyClient, PlaylistVerifyError
    from kexp.config import Config
    from tests.conftest import FakeResponse
    import pytest
    c=Config.from_env({"SPOTIFY_CLIENT_ID":"i","SPOTIFY_CLIENT_SECRET":"s",
        "SPOTIFY_REFRESH_TOKEN":"r","SPOTIFY_PLAYLIST_ID":"pl"})
    fake_session.queue("put",FakeResponse(200,{"snapshot_id":"S3"}))
    # Re-fetch after the write returns a SHORT set (only u1) — should raise.
    fake_session.queue("get",FakeResponse(200,{
        "snapshot_id":"S3",
        "items":[{"track":{"uri":"u1","id":"a","external_ids":{"isrc":"I1"},
                            "name":"Song","artists":[{"name":"X"}],
                            "album":{"release_date":"2001"}}}],
        "next":None,
    }))
    sc=SpotifyClient(fake_session,c,sleep=lambda *_:None)
    with pytest.raises(PlaylistVerifyError):
        sc.replace_with_verify("tok",["u1","u2"],{"u1","u2"})
