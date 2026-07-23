"""Pure rendering of the daily summary email.

No I/O, no SMTP — sending stays in `kexp.alerting`/`kexp.pipeline`. Given
today's added-track rows and (optionally) a near-dupe report from
`kexp.dedupe.classify`, produce a `(subject, body)` pair.
"""


def render_daily(rows, near_dupe_report, *, date_str, prefix):
    count = len(rows)
    subject = f"{prefix} — {date_str} ({count} track{'s' if count != 1 else ''})"

    if rows:
        lines = [f"- {r['artist']} — {r['song']}  ({r['spotify_url']})" for r in rows]
        list_block = "\n".join(lines)
    else:
        list_block = "(No tracks logged today.)"

    body = (
        f"John Richards — KEXP Morning Show (7–10am PT)\n"
        f"Date: {date_str}\n"
        f"Tracks added: {count}\n\n{list_block}\n"
    )

    if near_dupe_report:
        dupe_lines = []
        for group in near_dupe_report:
            artist = group.get("artist", "")
            names = ", ".join(
                sorted({item.get("name", "") for item in group.get("items", [])})
            )
            dupe_lines.append(f"- {artist}: {names}")
        body += ("\nPossible duplicates to review (version-distinct near-dupes among "
                 "existing playlist tracks, as of this run's playlist read):\n"
                 + "\n".join(dupe_lines) + "\n")

    body += "\n–––\nThis email was sent by your GitHub Action."

    return subject, body
