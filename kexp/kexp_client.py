"""KEXP plays API client + per-show host check (with failure-safe caching)."""
import sys

import requests

from kexp.http import request_json

KEXP_PLAYS_URL = "https://api.kexp.org/v2/plays/"
HOST_ID_JOHN = 26


class KexpClient:
    def __init__(self, session):
        self.session = session
        self._show_is_john_cache = {}  # show_uri -> bool; only successful lookups cached

    def fetch_plays(self, after_iso, before_iso, limit=200) -> list:
        params = {"airdate_after": after_iso, "airdate_before": before_iso, "limit": limit}
        # Route through request_json so a transient KEXP 5xx / network error retries
        # with backoff instead of crashing the whole run. api.kexp.org intermittently
        # returns 502 Bad Gateway; a bare raise_for_status here failed the entire job.
        r = request_json(self.session, "get", KEXP_PLAYS_URL, expect=200,
                         params=params, timeout=30)
        return (r.json() or {}).get("results") or []

    def is_john_show(self, show_uri) -> bool:
        if not show_uri:
            return False
        if show_uri in self._show_is_john_cache:
            return self._show_is_john_cache[show_uri]
        try:
            r = self.session.get(show_uri, timeout=15)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            # Do NOT memoize failures: a transient error must not mark this show
            # not-John for the rest of the run (would silently drop later plays).
            print(f"[is_john_show] lookup failed for {show_uri}: {e}", file=sys.stderr)
            return False
        hosts = data.get("hosts") or []
        host_names = [h.lower() for h in (data.get("host_names") or [])]
        result = (HOST_ID_JOHN in hosts) or any("john richards" in n for n in host_names)
        self._show_is_john_cache[show_uri] = result
        return result
