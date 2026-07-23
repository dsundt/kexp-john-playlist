"""Classify playlist duplicates (pure logic, no network).

Auto-removes only exact (track id OR ISRC) and "safe" near-dupes (identical
after normalize_title's case/punctuation-only normalization). Version-distinct
near-dupes (differ by remaster/live/edit/radio/mono/version/feat or a
parenthetical) are reported for operator review, never auto-removed. The
earliest occurrence of any group is always kept.
"""
from dataclasses import dataclass

from kexp.matching import normalize_title, strip_version


@dataclass
class DedupePlan:
    remove_positions: list
    report: list
    counts: dict


def classify(items):
    remove_positions = []
    report = []
    counts = {"exact": 0, "isrc": 0, "safe_near": 0, "reported": 0}

    seen_ids = set()
    seen_isrcs = set()
    seen_safe_keys = set()
    seen_version_keys = {}   # version_key -> first-seen item (kept row)
    report_entries = {}      # version_key -> report dict (once a group is reported)

    for item in items:
        pos = item["position"]
        tid = item.get("id")
        isrc = item.get("isrc")
        artist_norm = normalize_title(item.get("artist", ""))
        name = item.get("name", "")
        safe_key = (artist_norm, normalize_title(name))
        version_key = (artist_norm, strip_version(name))

        if tid is not None and tid in seen_ids:
            remove_positions.append(pos)
            counts["exact"] += 1
            continue
        if isrc is not None and isrc in seen_isrcs:
            remove_positions.append(pos)
            counts["isrc"] += 1
            continue
        if safe_key in seen_safe_keys:
            remove_positions.append(pos)
            counts["safe_near"] += 1
            continue

        # Not an auto-remove match — check for a version-distinct near-dupe
        # (shares the stripped-version key with an earlier, still-kept row).
        if version_key in seen_version_keys:
            entry = report_entries.get(version_key)
            if entry is None:
                entry = {
                    "artist": item.get("artist"),
                    "key": version_key,
                    "items": [seen_version_keys[version_key]],
                }
                report.append(entry)
                report_entries[version_key] = entry
                counts["reported"] += 1
            entry["items"].append(item)
        else:
            seen_version_keys[version_key] = item

        if tid is not None:
            seen_ids.add(tid)
        if isrc is not None:
            seen_isrcs.add(isrc)
        seen_safe_keys.add(safe_key)

    return DedupePlan(remove_positions=remove_positions, report=report, counts=counts)
