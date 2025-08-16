#!/usr/bin/env python3
import os, sys, json, time, re
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import requests

# ---- Config ----
FEED_PATH = "docs/feed.xml"         # Optional audit RSS (served by GitHub Pages if /docs is enabled)
SEEN_PATH = "data/seen.json"        # persistent dedupe + flags
LOG_NOT_FOUND = "data/not_found.csv"
FEED_TITLE = "KEXP — John Richards — Spotify Matches"
FEED_LINK = "https://www.kexp.org/schedule/"
FEED_DESC = "Tracks played during The Morning Show (KEXP) matched on Spotify; also auto-added to the target playlist."
KEXP_PLAYS_URL = "https://api.kexp.org/v2/plays/"
HOST_ID_JOHN = 26                   # John Richards
ROLLING_WINDOW_MINUTES = 12         # live/polling window, Action runs every 10 min

# Spotify OAuth (user)
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
SPOTIFY_ADD_URL_TPL = "https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

# Secrets / env
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
PLAYLIST_ID = os.getenv("SPOTIFY_PLAYLIST_ID")

# Backfill range controls
# Start from 2025-01-01 up to "today" by default (PT), weekdays only (Mon–Fri), 7–10am PT.
BACKFILL_START_DATE = os.getenv("BACKFILL_START_DATE", "2025-01-01")  # YYYY-MM-DD
BACKFILL_END_DATE = os.getenv("BACKFILL_END_DATE", "")                # YYYY-MM-DD (optional; empty = today PT)
FORCE_BACKFILL_ONCE = os.getenv("FORCE_BACKFILL_ONCE") == "1"         # ignore 'done' flag for a single replay

# Toggle adding to Spotify (1=yes, 0=no). Leave "1" to actually build the playlist.
DO_SPOTIFY_ADDS = os.getenv("DO_SPOTIFY_ADDS", "1") == "1"

# Debug toggles (optional)
JR_DEBUG = os.getenv("JR_DEBUG") == "1"
JR_SKIP_HOST_CHECK = os.getenv("JR_SKIP_HOST_CHECK") == "1"

PT = ZoneInfo("America/Los_Angeles")

# ---- Utilities ----
def ensure_dirs():
    os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(LOG_NOT_FOUND), exist_ok=True)

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

def log_not_found(artist, song, reason):
    new = not os.path.exists(LOG_NOT_FOUND)
    with open(LOG_NOT_FOUND, "a", encoding="utf-8") as f:
        if new:
            f.write("timestamp_pt,artist,song,reason\n")
        ts = datetime.now(PT).isoformat()
        f.write(f"{ts},{artist},{song},{reason}\n")

def ensure_feed_exists():
    if os.path.exists(FEED_PATH):
        return
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>{FEED_TITLE}</title>
<link>{FEED_LINK}</link>
<description>{FEED_DESC}</description>
<language>en-us</language>
<lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %Z")}</lastBuildDate>
</channel>
</rss>""")

def append_feed_item(title, link, guid, pubdate_iso):
    with open(FEED_PATH, "r", encoding="utf-8") as f:
        xml = f.read()
    insert_at = xml.rfind("</channel>")
    if insert_at == -1:
        ensure_feed_exists()
        with open(FEED_PATH, "r", encoding="utf-8") as f:
            xml = f.read()
        insert_at = xml.rfind("</channel>")
    item = f"""
<item>
  <title>{title}</title>
  <link>{link}</link>
  <guid isPermaLink="false">{guid}</guid>
  <pubDate>{pubdate_iso}</pubDate>
</item>"""
    new_xml = xml[:insert_at] + item + "\n" + xml[insert_at:]
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        f.write(new_xml)

def clean_title(s):
    if not s: return s
    s = re.sub(r"\s*\[[^\]]+\]\s*$", "", s)
    s = re.sub(r"\s*\([^)]+\)\s*$", "", s)       # (Live), (Remix)
    s = re.sub(r"\s+-\s+Remaster(ed)?\s*\d*$", "", s, flags=re.I)
    return s.strip()

# ---- Spotify auth & calls ----
def refresh_access_token():
    data = {"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN}
    r = requests.post(SPOTIFY_TOKEN_URL, data=data, auth=(CLIENT_ID, CLIENT_SECRET), timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def spotify_search_track(token, artist, song):
    q = f'track:"{song}" artist:"{artist}"'
    params = {"q": q, "type": "track", "limit": 1}
    r = requests.get(SPOTIFY_SEARCH_URL, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=20)
    r.raise_for_status()
    items = (r.json().get("tracks") or {}).get("items") or []
    return items[0] if items else None

def spotify_add_tracks(token, playlist_id, track_ids):
    """Add up to 100 tracks per call. track_ids are raw IDs; converted to URIs here."""
    if not DO_SPOTIFY_ADDS or not track_ids:
        return
    uris = [f"spotify:track:{tid}" for tid in track_ids]
    url = SPOTIFY_ADD_URL_TPL.format(playlist_id=playlist_id)
    payload = {"uris": uris}
    r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                      json=payload, timeout=30)
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", "10"))
        time.sleep(wait)
        r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                          json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

# ---- KEXP ----
def fetch_kexp_plays(airdate_after_iso, airdate_before_iso, limit=200):
    params = {"airdate_after": airdate_after_iso, "airdate_before": airdate_before_iso, "limit": limit}
    r = requests.get(KEXP_PLAYS_URL, params=params, timeout=30)
    r.raise_for_status()
    return (r.json() or {}).get("results") or []

def is_john_show(show_uri):
    if JR_SKIP_HOST_CHECK:
        return True
    if not show_uri:
        return False
    try:
        r = requests.get(show_uri, timeout=15)
        r.raise_for_status()
        data = r.json()
        hosts = data.get("hosts") or []
        if HOST_ID_JOHN in hosts:
            return True
        host_names = [h.lower() for h in (data.get("host_names") or [])]
        return any("john richards" in n for n in host_names)
    except Exception:
        return False

def is_morning_show_airdate(airdate_iso):
    try:
        dt = datetime.fromisoformat(airdate_iso)
    except Exception:
        return False
    dt_pt = dt.astimezone(PT)
    return (dt_pt.weekday() < 5) and (7 <= dt_pt.hour < 10)

# ---- Core window processing ----
def process_window(token, start_pt: datetime, end_pt: datetime, seen_keys: set):
    airdate_after = start_pt.isoformat()
    airdate_before = end_pt.isoformat()
    plays = fetch_kexp_plays(airdate_after, airdate_before)

    if JR_DEBUG:
        print(f"Fetched {len(plays)} plays between {airdate_after} and {airdate_before}")
        print("DEBUG sample plays:", json.dumps((plays or [])[:3], indent=2, default=str))

    track_ids_to_add = []
    added_feed = 0

    for p in plays:
        if p.get("play_type") != "trackplay":
            continue
        if not is_morning_show_airdate(p.get("airdate") or ""):
            continue
        if not is_john_show(p.get("show_uri")):
            continue

        artist = (p.get("artist") or "").strip()
        song = clean_title(p.get("song") or "")
        if not artist or not song:
            continue

        key = f'jr::{p.get("track_id") or (artist.lower()+"::"+song.lower())}'
        if key in seen_keys:
            continue

        # Spotify lookup (single retry for 429)
        try:
            tr = spotify_search_track(token, artist, song)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                time.sleep(int(e.response.headers.get("Retry-After", "60")))
                tr = spotify_search_track(token, artist, song)
            else:
                log_not_found(artist, song, f"spotify_http_{getattr(e.response,'status_code','?')}")
                continue
        except Exception:
            log_not_found(artist, song, "spotify_search_error")
            continue

        if not tr:
            log_not_found(artist, song, "not_found")
            seen_keys.add(key)  # optional: avoid retrying forever
            continue

        track_id = tr["id"]
        track_url = tr["external_urls"]["spotify"]
        title = f"{artist} — {song}"
        pub_dt = datetime.fromisoformat(p["airdate"])

        # Append to audit feed (optional but handy)
        append_feed_item(title=title, link=track_url, guid=track_id, pubdate_iso=pub_dt.isoformat())
        added_feed += 1

        track_ids_to_add.append(track_id)
        seen_keys.add(key)

    # Batch add to Spotify (100 per call)
    for i in range(0, len(track_ids_to_add), 100):
        batch = track_ids_to_add[i:i+100]
        if batch:
            spotify_add_tracks(token, PLAYLIST_ID, batch)

    if JR_DEBUG:
        print(f"Appended {added_feed} item(s) to feed; added {len(track_ids_to_add)} track(s) to playlist.")
    return added_feed, len(track_ids_to_add)

# ---- Backfill helpers ----
def parse_date_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def iter_weekdays_range(start_d: date, end_d: date):
    """Yield all weekdays (Mon–Fri) from start_d..end_d inclusive."""
    d = start_d
    while d <= end_d:
        if d.weekday() < 5:
            yield d
        d = d + timedelta(days=1)

def run_range_backfill(token, seen, start_str: str, end_str: str):
    """Backfill weekdays 7–10am PT from start_str..end_str inclusive, oldest→newest."""
    today_pt = datetime.now(PT).date()
    start_d = parse_date_ymd(start_str)
    end_d = parse_date_ymd(end_str) if end_str else today_pt
    if end_d > today_pt:
        end_d = today_pt

    seen_keys = set(seen.get("keys", []))
    total_feed = 0
    total_sp = 0

    for d in iter_weekdays_range(start_d, end_d):
        start_pt = datetime(d.year, d.month, d.day, 7, 0, 0, tzinfo=PT)
        end_pt   = datetime(d.year, d.month, d.day, 10, 0, 0, tzinfo=PT)

        if JR_DEBUG:
            print(f"[Backfill] {d.isoformat()} 07:00–10:00 PT")

        added_feed, added_sp = process_window(token, start_pt, end_pt, seen_keys)
        total_feed += added_feed
        total_sp += added_sp
        time.sleep(0.5)  # be gentle between days

    if seen.get("keys") != sorted(seen_keys):
        seen["keys"] = sorted(seen_keys)
        save_seen(seen)

    return (total_feed, total_sp)

def run_backfill_if_needed(token, seen):
    """Run the 2025→today backfill once (or when forced)."""
    done = seen.get("flags", {}).get("backfill_done_range", False)
    if done and not FORCE_BACKFILL_ONCE:
        return (0, 0), False

    start_str = BACKFILL_START_DATE or "2025-01-01"
    end_str = BACKFILL_END_DATE or ""  # empty = today

    total_feed, total_sp = run_range_backfill(token, seen, start_str, end_str)

    seen.setdefault("flags", {})["backfill_done_range"] = True
    seen["flags"]["backfill_last_start"] = start_str
    seen["flags"]["backfill_last_end"] = (end_str or datetime.now(PT).date().isoformat())
    save_seen(seen)

    return (total_feed, total_sp), True

# ---- Live polling ----
def run_live(token, seen):
    """Rolling 12-minute window polling."""
    now_pt = datetime.now(PT)
    end_pt = now_pt
    start_pt = now_pt - timedelta(minutes=ROLLING_WINDOW_MINUTES)
    seen_keys = set(seen.get("keys", []))
    added_feed, added_sp = process_window(token, start_pt, end_pt, seen_keys)
    if seen.get("keys") != sorted(seen_keys):
        seen["keys"] = sorted(seen_keys)
        save_seen(seen)
    return added_feed, added_sp

# ---- Main ----
def main():
    for name, val in [
        ("SPOTIFY_CLIENT_ID", CLIENT_ID),
        ("SPOTIFY_CLIENT_SECRET", CLIENT_SECRET),
        ("SPOTIFY_REFRESH_TOKEN", REFRESH_TOKEN),
        ("SPOTIFY_PLAYLIST_ID", PLAYLIST_ID),
    ]:
        if not val:
            print(f"Missing {name}", file=sys.stderr)
            sys.exit(1)

    ensure_dirs()
    ensure_feed_exists()

    seen = load_seen()
    token = refresh_access_token()

    # Full-range backfill (2025-01-01 → today), one-time unless forced
    (bf_feed, bf_sp), did_bf = run_backfill_if_needed(token, seen)
    if did_bf:
        print(f"Backfill 2025→today complete. Feed+Playlist added: {bf_feed}/{bf_sp}")

    # Live/rolling updates
    live_feed, live_sp = run_live(token, load_seen())  # reload in case backfill updated it
    print(f"Live window added → Feed: {live_feed}, Playlist: {live_sp}")

if __name__ == "__main__":
    main()
