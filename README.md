# KEXP — John Richards → Spotify playlist bot

Automatically mirrors the tracks played on **[KEXP](https://www.kexp.org)'s *The
Morning Show* with John Richards** into a Spotify playlist and an RSS feed. It runs
on a GitHub Actions cron, matches each play to a Spotify track, appends new matches
to a target playlist, and emails a once-per-day summary.

The playlist is kept clean automatically: safe de-duplication, deterministic
re-ordering, and add-time dedupe against what's already present.

---

## How it works

Every 10 minutes (gated to the live show hours) the pipeline:

1. **Refreshes** the Spotify token and fetches the target playlist once (used for
   add-time dedupe and the near-duplicate report).
2. **One-time backfill** — on first run, walks weekday morning shows from
   `BACKFILL_START_DATE` (default `2025-01-01`) → today and matches every play.
   Recorded as done in `data/seen.json` so it never repeats.
3. **Live window** — fetches KEXP plays for a rolling 12-minute window
   (`ROLLING_WINDOW_MINUTES`, overlapping the 10-minute cron so nothing is missed),
   keeps only *The Morning Show* trackplays whose host is **John Richards**
   (host id `26`), matches each on Spotify, and appends new tracks to the playlist +
   RSS feed. `data/seen.json` makes every add idempotent.
4. **Email** — after 10:10 AM PT on weekdays, sends one daily summary.
5. **Dedupe** (after 10:25 AM PT) — snapshot-guarded, URI-based `DELETE` of
   duplicates with kept exact-URI copies re-added; **never** a full-replace/truncate.
6. **Reorder** (after 10:20 AM PT) — backup → `replace_with_verify` → auto-restore
   if verification fails. Runs *last* so its ordered replace covers anything dedupe
   re-added.
7. **Heartbeat** — writes `data/heartbeat.json` with per-run counts.

The whole run is wrapped in a failure-alert email that re-raises so a failed run
still shows red in GitHub Actions.

### Resilience

All outbound HTTP goes through `request_json` (`kexp/http.py`), which retries
transient 5xx responses and network errors with backoff (4xx raises immediately).
KEXP's API intermittently returns `502 Bad Gateway`; the retry helper absorbs those
blips instead of failing the run or silently dropping a play.

---

## Project layout

```
kexp/
  pipeline.py       Orchestration — main() wires everything together
  config.py         Typed config loaded from the environment
  http.py           Shared requests.Session + retrying request_json helper
  kexp_client.py    KEXP plays API + per-show "is this John Richards?" check
  spotify_client.py Token refresh, playlist fetch/replace/verify
  matching.py       Artist/song → best Spotify track match, title normalization
  dedupe.py         Duplicate classification + near-duplicate report
  feed.py           RSS feed read/append/normalize (self-healing malformed XML)
  emailer.py        SMTP daily summary
  alerting.py       Failure-alert email + heartbeat
  backup.py         Playlist snapshot/backup helpers
scripts/run.py      Thin entrypoint: `python scripts/run.py`
tests/              pytest suite (fake session/env/clock — no network)
data/               seen.json, daily CSVs, backups, not_found.csv (committed state)
docs/feed.xml       Published RSS feed
```

---

## Running locally

```bash
pip install -r requirements.txt
# set the required Spotify env vars (see below), then:
python scripts/run.py
```

Set `DO_SPOTIFY_ADDS=0` for a **dry run**: the pipeline reads, matches, and logs
what it *would* do, but issues no add/PUT/POST/DELETE to Spotify.

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests drive `main(session=..., env=..., now=...)` with a fake session, env, and
clock — fully offline, no network or real Spotify calls.

---

## Configuration (environment variables)

**Required** (the run exits non-zero if any is missing):

| Variable | Purpose |
| --- | --- |
| `SPOTIFY_CLIENT_ID` | Spotify app client id |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret |
| `SPOTIFY_REFRESH_TOKEN` | OAuth refresh token for the playlist owner |
| `SPOTIFY_PLAYLIST_ID` | Target playlist id |

**Behavior toggles** (`1` = on; defaults shown):

| Variable | Default | Effect |
| --- | --- | --- |
| `DO_SPOTIFY_ADDS` | `1` | `0` = dry run (no playlist mutations) |
| `DO_EMAIL` | `1` | Send the daily summary email |
| `DO_DEDUPE` | `1` | Run daily duplicate removal |
| `DO_REORDER` | `1` | Run daily reorder |
| `FEED_MAX_ITEMS` | `200` | Max items kept in `docs/feed.xml` |

**Backfill & timing:**

| Variable | Default | Effect |
| --- | --- | --- |
| `BACKFILL_START_DATE` | `2025-01-01` | Oldest date for the one-time backfill |
| `BACKFILL_END_DATE` | *(empty)* | Empty = today (PT) |
| `FORCE_BACKFILL_ONCE` | `0` | `1` = re-run backfill even if already done |
| `REORDER_AFTER_HOUR_PT` / `REORDER_AFTER_MIN_PT` | `10` / `20` | Earliest reorder time (PT) |
| `DEDUPE_AFTER_HOUR_PT` / `DEDUPE_AFTER_MIN_PT` | `10` / `25` | Earliest dedupe time (PT) |

**Email (SMTP):** `SMTP_HOST`, `SMTP_PORT` (default `587`), `SMTP_USERNAME`,
`SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`, `MAIL_SUBJECT_PREFIX`
(default `KEXP — John`).

**Debug:** `JR_DEBUG=1` verbose logs · `JR_SKIP_HOST_CHECK=1` skip the
per-show John-Richards host check · `EMAIL_TEST_MODE=1` email path testing.

---

## Deployment

Runs via GitHub Actions (`.github/workflows/kexp.yml`) on a `*/10 * * * *` cron;
the script itself gates to 7–10 AM PT, Mon–Fri, and emails once per day after
10:10 AM PT. Secrets are supplied as repository secrets. Each run commits any
changes to `docs/feed.xml` and `data/`. A separate `tests.yml` runs the pytest
suite on every push and pull request.
