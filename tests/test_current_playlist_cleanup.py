"""Task 13: verify the one-time cleanup of the current 22 near-dup groups.

Fixture reconstructed from `near_dupe_review.txt` (the real playlist review).
Each group is two rows with different Spotify track ids (and distinct ISRCs),
same artist, and the two observed titles — so only the safe-key / version-key
logic in `kexp.dedupe.classify` decides remove-vs-report.

Expected (per the SHIPPED classify() semantics — punctuation-only differences
normalize away, only remaster/edit/parenthetical *version* tags are reported):
  * 19 groups are SAFE near-dupes -> auto-remove the later occurrence.
  * 3 groups are VERSION-DISTINCT -> reported, never removed:
      Frankie Goes To Hollywood — Relax (Come Fighting) / Relax
      Michael Kiwanuka — Cold Little Heart / Cold Little Heart - Radio Edit
      R.E.M. — Finest Worksong / Finest Worksong - Remastered

NOTE (deviation from the plan's "~4 reported, incl. Stop/Stop!"): "Stop" vs
"Stop!" differ only by punctuation, which `normalize_title` strips, so the
shipped classify() (asserted in Task 5's own test) treats it as a SAFE remove,
not a version-distinct report. This test pins the real, tested behavior.
"""
from kexp.dedupe import classify

# (artist, kept_title, dup_title) in observed (position) order.
SAFE_GROUPS = [
    ("Al Green", "Everybody Hurts", "Everybody Hurts"),
    ("Anna von Hausswolff", "Stardust", "Stardust"),
    ("Camper Van Beethoven", "Take the Skinheads Bowling", "Take the Skinheads Bowling"),
    ("Dove Ellis", "Love Is", "Love Is"),
    ("Eaves Wilder", "Everybody Talks", "Everybody Talks"),
    ("Ismay", "American Flag", "American Flag"),
    ("Jane's Addiction", "Stop", "Stop!"),                      # punctuation-only -> safe
    ("Jeff Buckley", "Last Goodbye", "Last Goodbye"),
    ("Louis Armstrong", "What A Wonderful World", "What A Wonderful World"),
    ("Nation of Language", "Inept Apollo", "Inept Apollo"),
    ("Nightbus", "Angles Mortz", "Angles Mortz"),
    ("Nina Simone", "Feeling Good", "Feeling Good"),
    ("Packaging, cindygod", "Running Through the Airport (feat. cindygod)",
                            "Running Through the Airport (feat. cindygod)"),
    ("Tears For Fears", "Mad World", "Mad World"),
    ("The Cranberries", "Dreams", "Dreams"),
    ("The Magnetic Fields", "All My Little Words", "All My Little Words"),
    ("The New Pornographers", "Ballad of the Last Payphone", "Ballad of the Last Payphone"),
    ("Twin Shadow", "Dominoes", "Dominoes"),
    ("Wolf Alice", "White Horses", "White Horses"),
]

REPORT_GROUPS = [
    ("Frankie Goes To Hollywood", "Relax (Come Fighting)", "Relax"),
    ("Michael Kiwanuka", "Cold Little Heart", "Cold Little Heart - Radio Edit"),
    ("R.E.M.", "Finest Worksong", "Finest Worksong - Remastered"),
]


def _build_items():
    items = []
    pos = 0
    n = 0
    for artist, a, b in SAFE_GROUPS + REPORT_GROUPS:
        for title in (a, b):
            n += 1
            items.append({
                "position": pos,
                "uri": f"spotify:track:id{n}",
                "id": f"id{n}",
                "isrc": f"ISRC{n:04d}",  # all distinct -> never an ISRC match
                "artist": artist,
                "name": title,
            })
            pos += 1
    return items


def test_current_playlist_cleanup_classification():
    items = _build_items()
    plan = classify(items)

    # 19 safe near-dupes removed (later occurrence of each), 3 reported.
    assert plan.counts["safe_near"] == 19
    assert plan.counts["reported"] == 3
    assert plan.counts["exact"] == 0
    assert plan.counts["isrc"] == 0
    assert len(plan.remove_positions) == 19

    reported_artists = {g["artist"] for g in plan.report}
    assert reported_artists == {
        "Frankie Goes To Hollywood", "Michael Kiwanuka", "R.E.M.",
    }
    # Stop/Stop! folds into the SAFE removals, not the report.
    assert "Jane's Addiction" not in reported_artists

    # Every removed position is the *second* row of a safe group (kept the first).
    safe_dup_positions = {i * 2 + 1 for i in range(len(SAFE_GROUPS))}
    assert set(plan.remove_positions) == safe_dup_positions
