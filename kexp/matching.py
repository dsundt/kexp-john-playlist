"""Title normalization, version-variant detection, and Spotify search fallback.

Pure logic — no network calls. `search_best` accepts an injected `search_fn`
so it can be unit tested without hitting Spotify.
"""
import re

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]\s*$")
_FEAT_RE = re.compile(r"\s*(?:feat\.?|featuring|ft\.?)\s+.*$", re.I)
_VERSION_SUFFIX_RE = re.compile(
    r"\s*[-–—:]?\s*"
    r"(?:re-?master(?:ed)?(?:\s*\d{4})?"
    r"|live(?:\s+at\s+.*)?"
    r"|radio\s+edit"
    r"|single\s+version"
    r"|album\s+version"
    r"|edit"
    r"|mono"
    r"|stereo"
    r"|version"
    r"|remix)"
    r"\s*$",
    re.I,
)


def normalize_title(s):
    """Lowercase, strip case/punctuation ONLY — no version-keyword stripping."""
    if not s:
        return ""
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def strip_version(s):
    """normalize_title(s) plus removal of trailing remaster/live/edit/radio/
    mono/version/remix suffixes and trailing parenthetical/bracket/feat tags.
    """
    if not s:
        return ""
    cur = s
    prev = None
    while cur != prev:
        prev = cur
        cur = _PAREN_RE.sub("", cur)
        cur = _BRACKET_RE.sub("", cur)
        cur = _FEAT_RE.sub("", cur)
        cur = _VERSION_SUFFIX_RE.sub("", cur)
        cur = cur.strip()
    return normalize_title(cur)


def is_version_variant(a, b):
    """True when a and b differ only by punctuation/case beyond a version tag —
    i.e. not identical after normalize_title, but identical after strip_version.
    """
    return normalize_title(a) != normalize_title(b) and strip_version(a) == strip_version(b)


def _pick_best(items, song):
    target = normalize_title(song)
    target_stripped = strip_version(song)

    exact = [it for it in items if normalize_title(it.get("name", "")) == target]
    if exact:
        return exact[0]

    stripped_matches = [it for it in items if strip_version(it.get("name", "")) == target_stripped]
    if stripped_matches:
        def adds_unwanted_variant(it):
            name_norm = normalize_title(it.get("name", ""))
            extra = name_norm.replace(target_stripped, "", 1)
            return bool(re.search(r"\bremix\b|\blive\b", extra))

        stripped_matches.sort(key=adds_unwanted_variant)
        return stripped_matches[0]

    return items[0] if items else None


def search_best(search_fn, artist, song):
    """Ordered fallback search: strict field query -> version-stripped query ->
    loose query. Returns the first non-empty result's best-matching item,
    preferring an exact normalized-title match and de-prioritizing items that
    add live/remix wording not present in the query.
    """
    queries = [f"artist:{artist} track:{song}"]

    stripped = strip_version(song)
    if stripped != normalize_title(song):
        queries.append(f"artist:{artist} track:{stripped}")

    queries.append(f"{artist} {song}")

    for query in queries:
        items = search_fn(query, {"type": "track", "limit": 10}) or []
        if not items:
            continue
        best = _pick_best(items, song)
        if best is not None:
            return best

    return None
