"""Playlist backup snapshots: write + prune.

No `datetime.now` inside — the caller injects `now_str` so writes are
deterministic and offline-testable.
"""
import json
import os


def write_backup(dir, snapshot_id, uris, *, now_str) -> str:
    """Write `<dir>/playlist-<now_str>.json` with the snapshot + uris; return its path."""
    os.makedirs(dir, exist_ok=True)
    uris = list(uris)
    path = os.path.join(dir, f"playlist-{now_str}.json")
    payload = {"snapshot_id": snapshot_id, "uris": uris, "count": len(uris)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def prune(dir, keep: int = 30) -> int:
    """Delete the oldest backup files beyond `keep` (by filename sort); return #deleted."""
    if not os.path.isdir(dir):
        return 0
    names = sorted(
        n for n in os.listdir(dir)
        if n.startswith("playlist-") and n.endswith(".json")
    )
    to_delete = names[:-keep] if keep > 0 else names
    for n in to_delete:
        os.remove(os.path.join(dir, n))
    return len(to_delete)
