"""Spotify client: auth, ISRC-aware read, snapshot-guarded safe DELETE, verified reorder.

Playlist mutations are intentionally NOT full-replace for dedupe: `remove_positions`
issues positional DELETEs (batched, all carrying the SAME snapshot_id) so a
duplicate-removal pass can never truncate the playlist to whatever page happened to
be read. `replace_with_verify` is for reorder only, and re-fetches + compares the
resulting URI set before returning success — callers must back up first and restore
from that backup if `PlaylistVerifyError` is raised.
"""
import sys
import time

import requests

from kexp.http import request_json

TOKEN_URL = "https://accounts.spotify.com/api/token"
PLAYLIST_ITEMS_TPL = "https://api.spotify.com/v1/playlists/{playlist_id}/tracks"


class PlaylistVerifyError(RuntimeError):
    """Raised when a post-replace re-fetch's URI set doesn't match what was written."""


class SpotifyClient:
    def __init__(self, session, config, sleep=time.sleep):
        self.session = session
        self.config = config
        self.sleep = sleep

    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def refresh_token(self) -> str:
        """Exchange the refresh token for an access token.

        Retries network errors and 5xx (mirrors kexp.http.request_json's backoff).
        Logs Spotify's error body on failure but NEVER the secret values
        themselves (lengths only). 4xx (invalid_grant/invalid_client) fails fast.
        """
        data = {"grant_type": "refresh_token", "refresh_token": self.config.refresh_token}
        auth = (self.config.client_id, self.config.client_secret)
        last_exc = None
        for attempt in range(3):
            try:
                r = self.session.post(TOKEN_URL, data=data, auth=auth, timeout=20)
            except requests.RequestException as e:
                last_exc = e
                print(f"[token-refresh] network error (attempt {attempt + 1}/3): {e}", file=sys.stderr)
                self.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r.json()["access_token"]
            print(
                f"[token-refresh] status={r.status_code} body={r.text} "
                f"(client_id_len={len(self.config.client_id or '')} "
                f"secret_len={len(self.config.client_secret or '')} "
                f"refresh_len={len(self.config.refresh_token or '')})",
                file=sys.stderr,
            )
            if 400 <= r.status_code < 500:
                r.raise_for_status()
            last_exc = requests.HTTPError(f"{r.status_code} on token refresh", response=r)
            self.sleep(2 * (attempt + 1))
        raise last_exc

    def fetch_playlist(self, token):
        """Return (snapshot_id, items). Each item: uri,id,isrc,name,artist,album_release_date,position."""
        items = []
        snapshot_id = None
        url = PLAYLIST_ITEMS_TPL.format(playlist_id=self.config.playlist_id)
        headers = {"Authorization": f"Bearer {token}"}
        limit = 100
        offset = 0
        position = 0
        while True:
            r = request_json(
                self.session, "get", url, expect=200, sleep=self.sleep,
                headers=headers, params={"limit": limit, "offset": offset}, timeout=30,
            )
            data = r.json()
            if snapshot_id is None:
                snapshot_id = data.get("snapshot_id")
            for it in (data.get("items") or []):
                tr = it.get("track") or {}
                if not tr:
                    position += 1
                    continue
                album = tr.get("album") or {}
                external_ids = tr.get("external_ids") or {}
                items.append({
                    "uri": tr.get("uri"),
                    "id": tr.get("id"),
                    "isrc": external_ids.get("isrc"),
                    "name": tr.get("name"),
                    "artist": ", ".join([a.get("name", "") for a in (tr.get("artists") or [])]),
                    "album_release_date": album.get("release_date") or "",
                    "position": position,
                })
                position += 1
            if data.get("next"):
                offset += limit
            else:
                break
        return snapshot_id, items

    def remove_positions(self, token, snapshot_id, uri_positions) -> None:
        """Remove tracks by (uri, positions) pairs, batched to <=100 objects per DELETE.

        Every batch carries the SAME snapshot_id so Spotify rejects (rather than
        silently reinterprets) a stale request — this is what makes dedupe
        non-truncating regardless of how many batches it takes.
        """
        headers = self._auth_headers(token)
        url = PLAYLIST_ITEMS_TPL.format(playlist_id=self.config.playlist_id)
        objects = [{"uri": uri, "positions": positions} for uri, positions in uri_positions]
        for i in range(0, len(objects), 100):
            batch = objects[i:i + 100]
            body = {"tracks": batch, "snapshot_id": snapshot_id}
            request_json(
                self.session, "delete", url, expect=200, sleep=self.sleep,
                headers=headers, json=body, timeout=30,
            )

    def reorder(self, token, ordered_uris) -> None:
        """Caller-supplied full reorder (PUT first 100 + POST rest). NOT used by dedupe."""
        headers = self._auth_headers(token)
        url = PLAYLIST_ITEMS_TPL.format(playlist_id=self.config.playlist_id)
        first = ordered_uris[:100]
        rest = ordered_uris[100:]
        request_json(
            self.session, "put", url, expect=200, sleep=self.sleep,
            headers=headers, json={"uris": first}, timeout=30,
        )
        for i in range(0, len(rest), 100):
            batch = rest[i:i + 100]
            request_json(
                self.session, "post", url, expect=201, sleep=self.sleep,
                headers=headers, json={"uris": batch}, timeout=30,
            )

    def replace_with_verify(self, token, ordered_uris, expected_set) -> bool:
        """Replace playlist contents (PUT first100 + POST rest), then verify.

        Backup is the caller's responsibility. After writing, re-fetches the
        playlist and compares its resulting URI set to `expected_set`; raises
        `PlaylistVerifyError` on mismatch so the caller can restore from backup
        and alert. Returns True on verified success.
        """
        self.reorder(token, ordered_uris)
        _, items = self.fetch_playlist(token)
        actual_set = {it["uri"] for it in items}
        if actual_set != expected_set:
            raise PlaylistVerifyError(
                f"post-replace verify failed: expected {len(expected_set)} uris, "
                f"got {len(actual_set)} (missing={len(expected_set - actual_set)}, "
                f"extra={len(actual_set - expected_set)})"
            )
        return True
