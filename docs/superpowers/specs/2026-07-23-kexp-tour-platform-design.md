# KEXP → Spotify + Tour Platform — Design Spec

**Date:** 2026-07-23
**Status:** Approved (brainstorming complete). Phase 1 implementing.
**Owner:** dsundt

## Overview

Evolve the KEXP "John in the Morning" → Spotify playlist bot (`scripts/run.py`, a
GitHub Action) from a single 700-line script into a maintainable package, harden the
risky playlist-mutation paths, and grow two new capabilities on top: a daily
"shows near Chicago" report and a Mission Control (MC) tour-dates surface.

Delivered in three phases, each its own branch → PR → CodeRabbit review → merge:

- **Phase 1 — `kexp` package + playlist safety.** Modular refactor, safe DELETE-based
  dedupe (ISRC-first → title-normalized hybrid), one-time cleanup of the current 22
  near-dupes, add-time dedupe, backup-before-writes, outage/failure alerting, better
  Spotify matching, unit tests.
- **Phase 2 — Events pipeline + daily shows email.** Ticketmaster (primary) + JamBase
  (backup, quota-guarded) providers → a canonical event store → daily "shows near
  Chicago" email. Adds venue-scoped lookups (a venue's full upcoming calendar).
- **Phase 3 — MC tour-dates surface.** An operator UI over the canonical store
  (direction: the v3 "Box Office × Radar" mockup), extensible provider/schema/UI seams.

## Canonical event model (shared by Phase 2 + 3)

One provider-agnostic schema every source maps INTO. Adding a provider or a feature
never touches the UI; it maps to, or reads from, this model. Defined once as a Zod
schema + DB migration (mirrors the workspace's F1 schema pattern).

```
Artist  { id, name, normalized_name, spotify_id, mbid?, image?, genres[], is_my_artist: bool }
Venue   { id, name, address, city, region, country, lat, lng, capacity?, provider_ids{} }
Event   { id (canonical, deduped across providers),
          lineup: [{ artist, role: headliner|support|festival }],
          tour_name?, type: concert|festival|livestream,
          starts_at_local, starts_at_utc, date_tba, doors_time?, ends_at_local?,
          venue, distance_miles,
          status: announced|on_sale|presale|sold_out|cancelled|postponed|rescheduled,
          offers: [{ provider, kind: primary|resale, url, price_min, price_max,
                     currency, on_sale_at?, presale[]?, get_in_price?, availability? }],
          sources: [{ provider, provider_event_id, last_seen_at }],
          first_seen_at, last_updated_at, note?,
          operator_state: none|saved|going|dismissed }
```

**Terminology:** "My Artists" = artists in the user's KEXP Spotify playlist (the flag
`is_my_artist` / `in_playlist`). The venue calendar is NOT limited to My Artists — it
shows a room's whole upcoming schedule, with My-Artist shows marked.

---

## Phase 1 — `kexp` package + playlist safety (THIS PR)

### Package layout
```
kexp/
  __init__.py
  config.py         # env -> typed Config (no import-time globals; testable)
  http.py           # shared requests.Session + one retry/429/backoff helper
  kexp_client.py    # fetch_plays, is_john_show (per-show cache, no failure memoization)
  matching.py       # title/artist normalization + fallback-query Spotify match, ISRC-aware
  spotify_client.py # auth (retry+error-body), search, playlist read, SAFE delete, reorder
  feed.py           # escape / self-heal / trim RSS (logic already shipped in main)
  dedupe.py         # exact(id/ISRC) + near-dupe classification (safe vs version-distinct)
  backup.py         # snapshot playlist before writes + prune old backups
  alerting.py       # failure email + last-success heartbeat
  emailer.py        # daily summary + near-dupe operator report section
  pipeline.py       # orchestration: backfill / live / dedupe / reorder / email
scripts/run.py      # thin entrypoint -> kexp.pipeline.main()
tests/              # pytest, one file per module, HTTP mocked
```
Thin `run.py` keeps the existing workflow command working unchanged.

### Safety: no more full-replace truncation
The current `replace_playlist_with_order()` does `PUT {first 100}` (wiping the playlist
to 100 tracks) then re-appends the rest across ~38 `POST`s — a mid-sequence failure
permanently truncates the 3,835-track playlist.

- **Dedupe → `DELETE /playlists/{id}/tracks`** with `{tracks:[{uri, positions:[...]}], snapshot_id}`,
  batched ≤100. Removes only the duplicate positions; the rest of the playlist is never
  touched. Truncation is structurally impossible.
- **Reorder** (must fully rewrite to sort by year) keeps a replace, but wrapped:
  **backup → replace → verify → auto-restore.** After replacing, re-read; if the track
  set/count doesn't match expected, restore from the backup snapshot and fire an alert.

### Dedupe + near-dupe (hybrid)
- **Exact:** same Spotify track id OR same ISRC (`external_ids.isrc` — first, exact
  "same recording" signal, zero false positives) → auto-remove later copies, keep earliest.
- **Near-dupe:** normalize title (strip case/punctuation only). Same `(artist,
  punct-stripped-title)` → "safe dupe" → auto-remove later copy. Differs only by a
  version keyword (remaster/live/edit/radio/mono/feat) → NOT auto-removed; listed in the
  operator report for a human decision.
- **One-time cleanup:** applies to the current playlist — 0 exact, 22 near-dup groups
  (~18 auto-safe, ~4 reported).

### Add-time dedupe
Fetch the live playlist's id-set + ISRC-set + safe-normalized-key-set once per run;
before adding a matched track, skip if already present (exact/ISRC/safe-near) and log it.
Dupes stop entering; daily dedupe becomes a true safety net; no longer sole reliance on
`seen.json`.

### Backup before writes
Before any mutation, write `data/backups/playlist-YYYYMMDD-HHMMSS.json` (`snapshot_id` +
ordered URIs), committed by the existing push step; prune to last ~30. Restore source for
reorder verify/restore.

### Outage / failure alerting
- `main()` wrapped: any unhandled exception → failure email via existing SMTP secrets →
  re-raise (Action still shows red). Would have caught the day-long revoked-token outage
  on day one.
- Write `data/last_success.json` (timestamp + counts) each clean run — a watchable heartbeat.

### Better Spotify matching
`matching.py` ordered fallback: (1) strict `track:"x" artist:"y"`; (2) retry with
parenthetical/feat/remaster stripped; (3) loose `x y`, pick best by artist+title
similarity, de-prioritizing live/remix unless the original title said so. Prefer ISRC
when KEXP supplies a resolvable recording id. Genuine misses still logged to
`not_found.csv`.

### Already shipped (PR #1, folded in): feed XML escaping/self-heal/trim, token-refresh
error logging + retry, is_john_show caching, shared Session, Node-24 actions, pinned
requirements, `.gitignore`.

### Testing / hardening (Phase 1)
`pytest`, HTTP mocked, no live API:
- config parsing; matching normalization + fallbacks + ISRC path
- exact(id/ISRC) + near-dupe classification (safe vs version-distinct)
- safe-DELETE batching + positions + snapshot guard
- reorder verify/restore (simulate truncation → assert restore + alert)
- backup write + prune
- add-time dedupe skip logic
- feed heal/trim; alerting trigger on exception
- an end-to-end dry-run (`DO_SPOTIFY_ADDS=0`, mocked providers) asserting no writes

---

## Phase 2 — Events pipeline + daily shows email

Runs locally (MC-side) into the canonical store as the single source of truth (fetch
once; the Action publishes `data/artists.json` as the My-Artists set). Config:
`SHOWS_ENABLE`, city/geo (Chicago), `SHOWS_RADIUS_MILES=30`, `SHOWS_WINDOW_DAYS=60`,
`JAMBASE_MONTHLY_CAP=900`. Secrets: `TICKETMASTER_API_KEY`, `JAMBASE_API_KEY`.

- **Provider interface** (`events/`): `map_to_canonical()` per source.
  - `TicketmasterProvider` (primary): geo `geoPoint`(Chicago)+`radius=30`+`unit=miles`+
    `classificationName=Music`+date window, paginated (~cheap vs 5k/day).
  - `JamBaseProvider` (backup, quota-guarded): geo lat/lng+radius via `/v3/events`;
    `data/jambase_usage.json` monthly counter hard-stops at cap (default 900). ~150/mo.
  - **Venue-scoped lookups** (powers the MC venue pane): TM `venueId` events / JamBase
    venue — a venue's FULL upcoming calendar, not limited to My Artists. Cached per venue.
- **Merge + dedupe** across sources by normalized (artist, date, venue); TM wins ties.
- **My-Artists filter** = membership against the playlist artist set (no per-artist calls).
- **Daily email:** "Upcoming shows near Chicago (My Artists)" over a rolling 60-day
  window, newly-detected shows highlighted (tracked in `data/shows_seen.json`).
- Tests: provider mappers (recorded fixtures), quota guard hard-stop, merge/dedupe,
  My-Artist filtering, venue-lookup cache, email rendering.

## Phase 3 — MC tour-dates surface

Direction: **v3 "Box Office × Radar"** mockup. MC page over the canonical store.
- Desktop: left 1/3 ticket-card index (My Artists) | right column split — top 1/3
  **venue pane** (venue meta + next 5 shows at that room, ALL artists, My-Artist rows
  marked), bottom 2/3 **radar** reacting to the selected card's location.
- Mobile: one column — radar → venue pane → swipeable synced card deck + scrubber/jump.
- Read-API filter-driven (date/distance/status/source/My-Artists/saved); single
  `setActive()` selection chokepoint.
- Operator-first: provider health, JamBase quota meter, freshness, provenance, sample/live
  banner. Follows existing MC patterns (Zod schema + migration, launchd service).
- **Extensibility seams:** provider interface (add source = one mapper); versioned
  canonical schema (additive); staged enrichment (dedupe→geocode→price→operator-state);
  filter-driven read-API so future features (on-sale alerts, ICS export, price-drop watch,
  "My Artists" as an add/remove action) are new consumers, not rewrites.

## Out of scope (YAGNI)
Multi-city/multi-host generality beyond config, StubHub as a first-class provider (resale
shown when a provider supplies it), ISRC-based *matching* against KEXP recording ids
beyond a best-effort pass.

## Open dependencies (operator)
- Phase 2/3 need `TICKETMASTER_API_KEY` (instant) and `JAMBASE_API_KEY` (free signup).
  Phase 2/3 build behind `SHOWS_ENABLE=0` and mocked tests until keys land.
