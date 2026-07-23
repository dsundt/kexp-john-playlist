from kexp.matching import normalize_title, strip_version, is_version_variant, search_best


def test_normalize_punct_case():
    assert normalize_title("Stop!") == normalize_title("stop")


def test_version_variant_detection():
    assert is_version_variant("Finest Worksong", "Finest Worksong - Remastered") is True
    assert is_version_variant("Feeling Good", "Feeling Good") is False


def test_strip_version_removes_trailing_suffixes():
    assert strip_version("Finest Worksong - Remastered") == strip_version("Finest Worksong")
    assert strip_version("Cold Little Heart (Radio Edit)") == strip_version("Cold Little Heart")


def test_search_best_falls_back_to_version_stripped_query():
    calls = []

    def fake_search(query, params):
        calls.append(query)
        if "remastered" in query.lower():
            return []
        return [{"name": "Finest Worksong", "artist": "R.E.M."}]

    result = search_best(fake_search, "R.E.M.", "Finest Worksong - Remastered")
    assert result is not None
    assert result["name"] == "Finest Worksong"
    assert len(calls) >= 2


def test_search_best_returns_none_when_no_hits():
    assert search_best(lambda q, p: [], "Nobody", "Nothing") is None
