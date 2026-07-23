from kexp.emailer import render_daily


def test_subject_includes_track_count_and_body_lists_tracks():
    rows = [
        {"artist": "A", "song": "S1", "spotify_url": "u1"},
        {"artist": "B", "song": "S2", "spotify_url": "u2"},
    ]
    subject, body = render_daily(rows, [], date_str="2026-07-23", prefix="KEXP — John")
    assert subject == "KEXP — John — 2026-07-23 (2 tracks)"
    assert "S1" in body and "u1" in body
    assert "S2" in body and "u2" in body
    assert "Possible duplicates to review" not in body


def test_subject_uses_singular_track_for_one_row():
    rows = [{"artist": "A", "song": "S1", "spotify_url": "u1"}]
    subject, _ = render_daily(rows, [], date_str="2026-07-23", prefix="KEXP")
    assert subject == "KEXP — 2026-07-23 (1 track)"


def test_subject_zero_tracks_and_empty_body_placeholder():
    subject, body = render_daily([], [], date_str="2026-07-23", prefix="KEXP")
    assert subject == "KEXP — 2026-07-23 (0 tracks)"
    assert "No tracks" in body


def test_near_dupe_section_appears_only_when_report_nonempty():
    report = [
        {
            "artist": "R",
            "key": ("r", "finest worksong"),
            "items": [
                {"name": "Finest Worksong"},
                {"name": "Finest Worksong - Remastered"},
            ],
        }
    ]
    subject, body = render_daily([], report, date_str="2026-07-23", prefix="KEXP")
    assert "Possible duplicates to review" in body
    assert "Finest Worksong" in body
    assert "R" in body
