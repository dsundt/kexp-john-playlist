from kexp.config import Config
def test_from_env_parses_and_flags():
    c = Config.from_env({"SPOTIFY_CLIENT_ID":"a","SPOTIFY_CLIENT_SECRET":"b",
        "SPOTIFY_REFRESH_TOKEN":"c","SPOTIFY_PLAYLIST_ID":"d","DO_DEDUPE":"0","FEED_MAX_ITEMS":"50"})
    assert c.client_id == "a" and c.do_dedupe is False and c.feed_max_items == 50
    assert c.do_reorder is True  # default
    assert c.missing() == []
def test_missing_lists_empty_required():
    assert set(Config.from_env({}).missing()) == {
        "SPOTIFY_CLIENT_ID","SPOTIFY_CLIENT_SECRET","SPOTIFY_REFRESH_TOKEN","SPOTIFY_PLAYLIST_ID"}
