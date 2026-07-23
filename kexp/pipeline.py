"""Pipeline orchestration: wire the kexp.* modules into the daily run.

Preserves the behavior of the original single-file scripts/run.py — weekday +
PT time gates (email after 10:10, reorder after 10:20, dedupe after 10:25),
once-per-day seen.json flags, one-time 2025->today backfill, the KEXP
morning-show (7-10am PT, weekdays, John Richards) window logic, and audit-feed
writes — but routes all HTTP through the tested kexp.* clients and adds the new
safety behaviors:

  * dedupe via snapshot-guarded uri-based DELETE + kept-copy re-add (never
    full-replace/truncate; only removed uris are touched);
  * reorder via backup -> replace_with_verify -> auto-restore on verify failure;
  * add-time dedupe (skip candidates already in the playlist by id/ISRC/safe key);
  * a failure-alert email wrapped around the whole run (then re-raise so the
    GitHub Action still shows red).

`dry_run` (== not config.do_spotify_adds) short-circuits every mutating client
call: no add/PUT/POST/DELETE is issued, but the run still reads, logs what it
WOULD do, and writes the heartbeat.

Dependency injection: `main(session=..., env=..., now=...)` lets tests drive the
whole flow with a fake session / env / clock; production calls `main()` bare.
"""
import csv
import json
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests

from kexp import alerting
from kexp import backup as backup_mod
from kexp import emailer
from kexp import feed as feed_mod
from kexp.config import Config
from kexp.dedupe import classify
from kexp.http import make_session, request_json
from kexp.kexp_client import KexpClient
from kexp.matching import normalize_title, search_best
from kexp.spotify_client import SpotifyClient, PlaylistVerifyError

PT = ZoneInfo("America/Los_Angeles")

# ---- Paths (repo-relative; the GitHub Action runs from the repo root) ----
FEED_PATH = "docs/feed.xml"
SEEN_PATH = "data/seen.json"
LOG_NOT_FOUND = "data/not_found.csv"
DAILY_DIR = "data/daily"
BACKUP_DIR = "data/backups"
HEARTBEAT_PATH = "data/heartbeat.json"

FEED_TITLE = "KEXP — John Richards — Spotify Matches"
FEED_LINK = "https://www.kexp.org/schedule/"
FEED_DESC = ("Tracks played during The Morning Show (KEXP) matched on Spotify; "
             "also auto-added to the target playlist.")

SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
PLAYLIST_ITEMS_TPL = "https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

ROLLING_WINDOW_MINUTES = 12


# --------------------------------------------------------------------------- #
# Runtime options that are NOT part of Config (kept on the env contract).
# --------------------------------------------------------------------------- #
@dataclass
class Options:
    backfill_start: str
    backfill_end: str
    force_backfill: bool
    email_test_mode: bool
    debug: bool
    skip_host_check: bool
    reorder_hour: int
    reorder_min: int
    dedupe_hour: int
    dedupe_min: int


def options_from_env(env) -> Options:
    return Options(
        backfill_start=env.get("BACKFILL_START_DATE", "2025-01-01") or "2025-01-01",
        backfill_end=env.get("BACKFILL_END_DATE", "") or "",
        force_backfill=env.get("FORCE_BACKFILL_ONCE") == "1",
        email_test_mode=env.get("EMAIL_TEST_MODE", "0") == "1",
        debug=env.get("JR_DEBUG") == "1",
        skip_host_check=env.get("JR_SKIP_HOST_CHECK") == "1",
        reorder_hour=int(env.get("REORDER_AFTER_HOUR_PT", "10") or "10"),
        reorder_min=int(env.get("REORDER_AFTER_MIN_PT", "20") or "20"),
        dedupe_hour=int(env.get("DEDUPE_AFTER_HOUR_PT", "10") or "10"),
        dedupe_min=int(env.get("DEDUPE_AFTER_MIN_PT", "25") or "25"),
    )


# --------------------------------------------------------------------------- #
# Small local ports of the original single-file helpers.
# --------------------------------------------------------------------------- #
def ensure_dirs():
    for p in [FEED_PATH, SEEN_PATH, LOG_NOT_FOUND]:
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
    os.makedirs(DAILY_DIR, exist_ok=True)


def load_seen():
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("keys", [])
                data.setdefault("flags", {})
                return data
        except Exception:
            pass
    return {"keys": [], "flags": {}}


def save_seen(seen):
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def log_not_found(artist, song, reason, *, now_pt=None):
    new = not os.path.exists(LOG_NOT_FOUND)
    with open(LOG_NOT_FOUND, "a", encoding="utf-8") as f:
        if new:
            f.write("timestamp_pt,artist,song,reason\n")
        ts = (now_pt or datetime.now(PT)).isoformat()
        f.write(f"{ts},{artist},{song},{reason}\n")


_CLEAN_BRACKET_RE = re.compile(r"\s*\[[^\]]+\]\s*$")
_CLEAN_PAREN_RE = re.compile(r"\s*\([^)]+\)\s*$")
_CLEAN_REMASTER_RE = re.compile(r"\s+-\s+Remaster(ed)?\s*\d*$", re.I)


def clean_title(s):
    """Port of the original clean_title: strip trailing bracket/paren/remaster."""
    if not s:
        return s
    s = _CLEAN_BRACKET_RE.sub("", s)
    s = _CLEAN_PAREN_RE.sub("", s)
    s = _CLEAN_REMASTER_RE.sub("", s)
    return s.strip()


def daily_csv_path(day_pt: date) -> str:
    return os.path.join(DAILY_DIR, f"{day_pt.isoformat()}.csv")


def daily_log_append(day_pt, artist, song, track_url, track_id, *, now_pt=None):
    path = daily_csv_path(day_pt)
    new_file = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time_pt", "artist", "song", "spotify_url", "spotify_id"])
        stamp = (now_pt or datetime.now(PT)).strftime("%H:%M:%S")
        w.writerow([stamp, artist, song, track_url, track_id])


def read_daily_rows(day_pt: date):
    path = daily_csv_path(day_pt)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def is_morning_show_airdate(airdate_iso):
    try:
        dt = datetime.fromisoformat(airdate_iso)
    except Exception:
        return False
    dt_pt = dt.astimezone(PT)
    return (dt_pt.weekday() < 5) and (7 <= dt_pt.hour < 10)


def _safe_year_from_release_date(date_str) -> int:
    if not date_str:
        return 9999
    try:
        return int(str(date_str)[:4])
    except Exception:
        return 9999


# --------------------------------------------------------------------------- #
# Add-time dedupe membership.
# --------------------------------------------------------------------------- #
def build_membership(items):
    """Return (ids, isrcs, safe_keys) sets for add-time dedupe.

    safe_keys = (normalize_title(artist), normalize_title(name)) — the same
    "safe near-dupe" key kexp.dedupe uses, so an add that would create a safe
    duplicate is skipped before it is ever written.
    """
    ids = set()
    isrcs = set()
    safe_keys = set()
    for it in items:
        tid = it.get("id")
        isrc = it.get("isrc")
        if tid:
            ids.add(tid)
        if isrc:
            isrcs.add(isrc)
        safe_keys.add((normalize_title(it.get("artist", "")),
                       normalize_title(it.get("name", ""))))
    return ids, isrcs, safe_keys


# --------------------------------------------------------------------------- #
# Dedupe (uri-based DELETE + kept-copy re-add, snapshot-guarded, backup-first).
# --------------------------------------------------------------------------- #
def _positions_to_uri_removals(items, positions):
    """Convert flat playlist positions into per-uri removal descriptors.

    Spotify's DELETE removes ALL occurrences of a supplied uri (per-uri
    `positions` are no longer honored), so we describe each removed uri by how
    many copies it has (`total_count`) and how many to drop (`remove_count`).
    `SpotifyClient.remove_duplicate_uris` deletes the uri then re-adds
    `total_count - remove_count` kept copies. Returns
    ``[{"uri", "remove_count", "total_count"}, ...]``.
    """
    pos_to_uri = {it["position"]: it["uri"] for it in items}
    total_by_uri = {}
    for it in items:
        total_by_uri[it["uri"]] = total_by_uri.get(it["uri"], 0) + 1
    remove_by_uri = {}
    order = []
    for pos in positions:
        uri = pos_to_uri.get(pos)
        if uri is None:
            continue
        if uri not in remove_by_uri:
            remove_by_uri[uri] = 0
            order.append(uri)
        remove_by_uri[uri] += 1
    return [
        {"uri": uri, "remove_count": remove_by_uri[uri],
         "total_count": total_by_uri.get(uri, remove_by_uri[uri])}
        for uri in order
    ]


def run_dedupe(client, token, playlist_id, backup_dir, *, now_str, dry_run):
    """Fetch -> classify -> (live) backup + snapshot-guarded uri-based DELETE.

    Returns (removed_count, report). In dry_run performs NO mutating client call
    and writes NO backup, but still returns what WOULD be removed + the
    version-distinct near-dupe report for operator review.
    """
    snapshot_id, items = client.fetch_playlist(token)
    plan = classify(items)
    removed = len(plan.remove_positions)

    if plan.remove_positions and not dry_run:
        # Back up the exact snapshot we are about to mutate BEFORE deleting.
        backup_mod.write_backup(
            backup_dir, snapshot_id, [it["uri"] for it in items], now_str=now_str
        )
        removals = _positions_to_uri_removals(items, plan.remove_positions)
        client.remove_duplicate_uris(token, snapshot_id, removals)

    return removed, plan.report


# --------------------------------------------------------------------------- #
# Reorder (backup -> replace_with_verify -> auto-restore on verify failure).
# --------------------------------------------------------------------------- #
def _compute_reorder(items):
    """Return de-duped, year-sorted ordered_uris (keep first id occurrence).

    NOTE: the original sorted by (year, album, artist, track); the tested
    SpotifyClient.fetch_playlist does not surface album *name*, so the album
    tie-break is dropped -> (year, artist, track). See report §deviations.
    """
    sortable = []
    for it in items:
        year = _safe_year_from_release_date(it.get("album_release_date"))
        artist = (it.get("artist") or "").lower()
        name = (it.get("name") or "").lower()
        sortable.append((year, artist, name, it["uri"], it.get("id")))
    sortable.sort(key=lambda t: (t[0], t[1], t[2]))

    seen_ids = set()
    ordered = []
    for (_, _, _, uri, tid) in sortable:
        if tid and tid in seen_ids:
            continue
        if tid:
            seen_ids.add(tid)
        ordered.append(uri)
    return ordered


def run_reorder(client, config, token, backup_dir, *, now_str, dry_run):
    """Backup -> replace_with_verify -> restore-on-verify-failure.

    Returns (changed, reason). On PlaylistVerifyError, restores the playlist to
    the backed-up order and sends a failure alert (never leaves it truncated).
    """
    snapshot_id, items = client.fetch_playlist(token)
    if not items:
        return False, "No items"

    current_uris = [it["uri"] for it in items]
    ordered_uris = _compute_reorder(items)
    if ordered_uris == current_uris:
        return False, "Already in year order (no dups)"

    if dry_run:
        return False, f"[dry-run] would reorder {len(ordered_uris)} items"

    backup_mod.write_backup(backup_dir, snapshot_id, current_uris, now_str=now_str)
    expected_set = set(ordered_uris)
    try:
        client.replace_with_verify(token, ordered_uris, expected_set)
    except PlaylistVerifyError as e:
        # Restore original order from the backup, then alert loudly.
        try:
            client.reorder(token, current_uris)
        except Exception as restore_err:  # noqa: BLE001 - best-effort restore
            print(f"[reorder] restore FAILED after verify error: {restore_err}",
                  file=sys.stderr)
        alerting.send_failure_email(
            config,
            subject=f"{config.mail_subject_prefix} — REORDER VERIFY FAILED (restored)",
            body=(f"Playlist reorder verification failed and the playlist was "
                  f"restored from backup.\n\n{e}"),
        )
        backup_mod.prune(backup_dir, keep=30)
        return False, f"Verify failed; restored ({e})"

    backup_mod.prune(backup_dir, keep=30)
    return True, f"Reordered {len(ordered_uris)} items by year/artist/track (deduped)"


# --------------------------------------------------------------------------- #
# Add path (live + backfill) with add-time dedupe.
# --------------------------------------------------------------------------- #
def _make_search_fn(session, token, sleep=time.sleep):
    headers = {"Authorization": f"Bearer {token}"}

    def search_fn(query, params):
        p = {"q": query, **params}
        r = request_json(session, "get", SPOTIFY_SEARCH_URL, expect=200,
                         sleep=sleep, headers=headers, params=p, timeout=20)
        return ((r.json().get("tracks") or {}).get("items")) or []

    return search_fn


def _add_uris(session, token, playlist_id, uris, sleep=time.sleep):
    if not uris:
        return
    url = PLAYLIST_ITEMS_TPL.format(playlist_id=playlist_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for i in range(0, len(uris), 100):
        batch = uris[i:i + 100]
        request_json(session, "post", url, expect=201, sleep=sleep,
                     headers=headers, json={"uris": batch}, timeout=30)


def process_window(kx, session, token, config, start_pt, end_pt, seen_keys,
                   membership, *, dry_run, opts, now_pt):
    """Process one KEXP window: match -> add-time-dedupe -> feed/csv/add.

    `membership` is the (ids, isrcs, safe_keys) tuple; it is mutated in place so
    later windows in the same run also skip just-added tracks.
    """
    ids, isrcs, safe_keys = membership
    plays = kx.fetch_plays(start_pt.isoformat(), end_pt.isoformat())

    search_fn = _make_search_fn(session, token)
    uris_to_add = []
    added_feed = 0
    today_pt = now_pt.date()

    for p in plays:
        if p.get("play_type") != "trackplay":
            continue
        if not is_morning_show_airdate(p.get("airdate") or ""):
            continue
        if not opts.skip_host_check and not kx.is_john_show(p.get("show_uri")):
            continue

        artist = (p.get("artist") or "").strip()
        song = clean_title(p.get("song") or "")
        if not artist or not song:
            continue

        key = f'jr::{p.get("track_id") or (artist.lower() + "::" + song.lower())}'
        if key in seen_keys:
            continue

        try:
            tr = search_best(search_fn, artist, song)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", "?")
            log_not_found(artist, song, f"spotify_http_{code}", now_pt=now_pt)
            continue
        except Exception:
            log_not_found(artist, song, "spotify_search_error", now_pt=now_pt)
            continue

        if not tr:
            log_not_found(artist, song, "not_found", now_pt=now_pt)
            seen_keys.add(key)
            continue

        tid = tr.get("id")
        isrc = (tr.get("external_ids") or {}).get("isrc")
        tname = tr.get("name") or song
        tartist = ", ".join(a.get("name", "") for a in (tr.get("artists") or [])) or artist
        safe_key = (normalize_title(tartist), normalize_title(tname))

        # Add-time dedupe: skip anything already in the playlist.
        if (tid and tid in ids) or (isrc and isrc in isrcs) or (safe_key in safe_keys):
            print(f"[add-dedupe] skip already-present: {tartist} — {tname}", file=sys.stderr)
            seen_keys.add(key)
            continue

        track_url = (tr.get("external_urls") or {}).get("spotify", "")
        title = f"{artist} — {song}"
        try:
            pub_dt = datetime.fromisoformat(p["airdate"])
            pub_iso = pub_dt.isoformat()
        except Exception:
            pub_iso = now_pt.isoformat()

        feed_mod.append_item(FEED_PATH, title, track_url, tid or title, pub_iso,
                             max_items=config.feed_max_items)
        added_feed += 1
        daily_log_append(today_pt, artist, song, track_url, tid, now_pt=now_pt)

        if tr.get("uri"):
            uris_to_add.append(tr["uri"])
        # Update membership so later plays in this run don't re-add.
        if tid:
            ids.add(tid)
        if isrc:
            isrcs.add(isrc)
        safe_keys.add(safe_key)
        seen_keys.add(key)

    if not dry_run:
        _add_uris(session, token, config.playlist_id, uris_to_add)

    return added_feed, len(uris_to_add)


# --------------------------------------------------------------------------- #
# Backfill (one-time, weekdays 7-10am PT, oldest -> newest).
# --------------------------------------------------------------------------- #
def _parse_ymd(s) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _iter_weekdays(start_d, end_d):
    d = start_d
    while d <= end_d:
        if d.weekday() < 5:
            yield d
        d = d + timedelta(days=1)


def run_backfill_if_needed(kx, session, token, config, seen, membership, *,
                           opts, now_pt, dry_run):
    done = seen.get("flags", {}).get("backfill_done_range", False)
    if done and not opts.force_backfill:
        return (0, 0), False

    today_pt = now_pt.date()
    start_d = _parse_ymd(opts.backfill_start or "2025-01-01")
    end_d = _parse_ymd(opts.backfill_end) if opts.backfill_end else today_pt
    if end_d > today_pt:
        end_d = today_pt

    seen_keys = set(seen.get("keys", []))
    total_feed = total_sp = 0
    for d in _iter_weekdays(start_d, end_d):
        start_pt = datetime(d.year, d.month, d.day, 7, 0, 0, tzinfo=PT)
        end_pt = datetime(d.year, d.month, d.day, 10, 0, 0, tzinfo=PT)
        af, asp = process_window(kx, session, token, config, start_pt, end_pt,
                                 seen_keys, membership, dry_run=dry_run,
                                 opts=opts, now_pt=now_pt)
        total_feed += af
        total_sp += asp

    if seen.get("keys") != sorted(seen_keys):
        seen["keys"] = sorted(seen_keys)
    seen.setdefault("flags", {})["backfill_done_range"] = True
    seen["flags"]["backfill_last_start"] = opts.backfill_start
    seen["flags"]["backfill_last_end"] = opts.backfill_end or today_pt.isoformat()
    save_seen(seen)
    return (total_feed, total_sp), True


def run_live(kx, session, token, config, seen, membership, *, opts, now_pt, dry_run):
    end_pt = now_pt
    start_pt = now_pt - timedelta(minutes=ROLLING_WINDOW_MINUTES)
    seen_keys = set(seen.get("keys", []))
    af, asp = process_window(kx, session, token, config, start_pt, end_pt,
                             seen_keys, membership, dry_run=dry_run,
                             opts=opts, now_pt=now_pt)
    if seen.get("keys") != sorted(seen_keys):
        seen["keys"] = sorted(seen_keys)
        save_seen(seen)
    return af, asp


# --------------------------------------------------------------------------- #
# Daily summary email (time-gated, once per day).
# --------------------------------------------------------------------------- #
def _send_email(config, subject, body, smtp_factory=smtplib.SMTP):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.mail_from
    msg["To"] = config.mail_to
    msg.set_content(body)
    with smtp_factory(config.smtp_host, config.smtp_port, timeout=30) as s:
        s.ehlo()
        try:
            s.starttls()
            s.ehlo()
        except Exception:
            pass
        s.login(config.smtp_username, config.smtp_password)
        s.send_message(msg)


def maybe_send_email(config, seen, near_report, *, now_pt, opts, smtp_factory=smtplib.SMTP):
    if not config.do_email:
        return False, "Email disabled"

    required = [config.smtp_host, config.smtp_port, config.smtp_username,
                config.smtp_password, config.mail_from, config.mail_to]
    if any(not x for x in required):
        return False, "Missing SMTP secrets"

    if not opts.email_test_mode:
        if now_pt.weekday() >= 5 or (now_pt.hour < 10 or (now_pt.hour == 10 and now_pt.minute < 10)):
            return False, "Too early or weekend"

    key = f"email_sent_{now_pt.date().isoformat()}"
    if seen.get("flags", {}).get(key) and not opts.email_test_mode:
        return False, "Already sent today"

    rows = read_daily_rows(now_pt.date())
    if not rows and not opts.email_test_mode:
        return False, "No rows for today"

    date_str = now_pt.date().isoformat()
    subject, body = emailer.render_daily(rows, near_report, date_str=date_str,
                                         prefix=config.mail_subject_prefix)
    if opts.email_test_mode:
        subject = "[TEST] " + subject

    _send_email(config, subject, body, smtp_factory=smtp_factory)

    if not opts.email_test_mode:
        seen.setdefault("flags", {})[key] = True
        save_seen(seen)
    return True, "Sent"


# --------------------------------------------------------------------------- #
# Time-gated reorder / dedupe wrappers (weekday + PT gate + once-per-day flag).
# --------------------------------------------------------------------------- #
def _gate_ok(now_pt, hour, minute):
    if now_pt.weekday() >= 5:
        return False, "Weekend"
    if now_pt.hour < hour or (now_pt.hour == hour and now_pt.minute < minute):
        return False, "Too early"
    return True, ""


def maybe_reorder(client, config, token, seen, *, now_pt, now_str, opts, dry_run):
    if not config.do_reorder:
        return False, "Reorder disabled"
    ok, why = _gate_ok(now_pt, opts.reorder_hour, opts.reorder_min)
    if not ok:
        return False, why
    key = f"reorder_done_{now_pt.date().isoformat()}"
    if seen.get("flags", {}).get(key):
        return False, "Already reordered today"

    changed, reason = run_reorder(client, config, token, BACKUP_DIR,
                                  now_str=now_str, dry_run=dry_run)
    if not dry_run:
        seen.setdefault("flags", {})[key] = True
        save_seen(seen)
    return changed, reason


def maybe_dedupe(client, config, token, seen, *, now_pt, now_str, opts, dry_run):
    if not config.do_dedupe:
        return False, "Dedupe disabled", 0, []
    ok, why = _gate_ok(now_pt, opts.dedupe_hour, opts.dedupe_min)
    if not ok:
        return False, why, 0, []
    key = f"dedupe_done_{now_pt.date().isoformat()}"
    if seen.get("flags", {}).get(key):
        return False, "Already deduped today", 0, []

    removed, report = run_dedupe(client, token, config.playlist_id, BACKUP_DIR,
                                 now_str=now_str, dry_run=dry_run)
    if not dry_run:
        backup_mod.prune(BACKUP_DIR, keep=30)
        seen.setdefault("flags", {})[key] = True
        save_seen(seen)
    return removed > 0, f"Removed {removed} duplicate track(s)", removed, report


# --------------------------------------------------------------------------- #
# Entry point.
# --------------------------------------------------------------------------- #
def main(session=None, env=None, now=None):
    env = env if env is not None else os.environ
    config = Config.from_env(env)

    missing = config.missing()
    if missing:
        print("Missing " + ", ".join(missing), file=sys.stderr)
        sys.exit(1)

    opts = options_from_env(env)
    dry_run = not config.do_spotify_adds
    session = session if session is not None else make_session()
    sp = SpotifyClient(session, config)
    kx = KexpClient(session)

    now_pt = now if now is not None else datetime.now(PT)
    now_str = now_pt.strftime("%Y%m%dT%H%M%S")

    counts = {"feed_added": 0, "spotify_added": 0, "removed": 0, "reported": 0}
    try:
        ensure_dirs()
        feed_mod.ensure_exists(FEED_PATH, FEED_TITLE, FEED_LINK, FEED_DESC)
        if feed_mod.normalize_if_needed(FEED_PATH, max_items=config.feed_max_items):
            print("Feed normalized (healed malformed XML and/or trimmed to cap).")

        token = sp.refresh_token()

        # Pre-fetch the playlist once for add-time dedupe + the near-dupe report.
        _snap, pl_items = sp.fetch_playlist(token)
        membership = build_membership(pl_items)
        near_report = classify(pl_items).report

        seen = load_seen()
        (bf_feed, bf_sp), did_bf = run_backfill_if_needed(
            kx, session, token, config, seen, membership,
            opts=opts, now_pt=now_pt, dry_run=dry_run)
        if did_bf:
            print(f"Backfill 2025→today complete. Feed+Playlist added: {bf_feed}/{bf_sp}"
                  + (" [dry-run]" if dry_run else ""))

        live_feed, live_sp = run_live(
            kx, session, token, config, load_seen(), membership,
            opts=opts, now_pt=now_pt, dry_run=dry_run)
        print(f"Live window added → Feed: {live_feed}, Playlist: {live_sp}"
              + (" [dry-run]" if dry_run else ""))
        counts["feed_added"] = bf_feed + live_feed
        counts["spotify_added"] = bf_sp + live_sp

        sent, reason = maybe_send_email(config, load_seen(), near_report,
                                        now_pt=now_pt, opts=opts)
        print(f"Email status: {sent} ({reason})")

        changed, why = maybe_reorder(sp, config, token, load_seen(),
                                     now_pt=now_pt, now_str=now_str,
                                     opts=opts, dry_run=dry_run)
        print(f"Reorder status: {changed} ({why})")

        deduped, why2, removed, report = maybe_dedupe(
            sp, config, token, load_seen(),
            now_pt=now_pt, now_str=now_str, opts=opts, dry_run=dry_run)
        print(f"Dedupe status: {deduped} ({why2})")
        counts["removed"] = removed
        counts["reported"] = len(report or near_report)

        alerting.write_heartbeat(HEARTBEAT_PATH, now_str=now_pt.isoformat(), counts=counts)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - alert then re-raise so the Action shows red
        try:
            alerting.send_failure_email(
                config,
                subject=f"{config.mail_subject_prefix} — RUN FAILED",
                body=f"The KEXP run raised an unhandled error:\n\n{type(e).__name__}: {e}",
            )
        except Exception as alert_err:  # noqa: BLE001
            print(f"[alert] failure email also failed: {alert_err}", file=sys.stderr)
        raise

    return counts


if __name__ == "__main__":
    main()
