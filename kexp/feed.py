"""Audit-feed (RSS) helpers: escape, self-heal, trim, append.

Ported verbatim (behavior-for-behavior) from the functions that shipped in
scripts/run.py (_ITEM_RE, _STRAY_AMP_RE, _feed_header, _build_item,
_read_feed_items, _write_feed, ensure_feed_exists, normalize_feed_if_needed,
append_feed_item), parameterized by path/max_items/title/link/desc instead of
module-level globals.

Channel metadata (title/link/desc) is only needed to create a brand-new feed
(`ensure_exists`); once a feed exists, its header/preamble is preserved as-is
across `append_item`/`normalize_if_needed` calls — only the <item> blocks and
XML well-formedness are managed by this module.
"""
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape, unescape as xml_unescape

# Match one <item>...</item> block (used to parse/trim the feed).
_ITEM_RE = re.compile(r"[ \t]*<item>.*?</item>\n?", re.DOTALL)
# Match a bare & that is NOT already the start of a valid entity (legacy self-heal).
_STRAY_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)")

# Field extractors for rebuilding channel metadata / items when the on-disk feed
# is malformed beyond a stray '&' (e.g. a broken preamble or item content).
_CH_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_CH_LINK_RE = re.compile(r"<link>(.*?)</link>", re.DOTALL)
_CH_DESC_RE = re.compile(r"<description>(.*?)</description>", re.DOTALL)
_IT_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_IT_LINK_RE = re.compile(r"<link>(.*?)</link>", re.DOTALL)
_IT_GUID_RE = re.compile(r"<guid\b[^>]*>(.*?)</guid>", re.DOTALL)
_IT_PUB_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.DOTALL)

_DEFAULT_TITLE = "KEXP — audit feed"
_DEFAULT_LINK = "https://www.kexp.org/schedule/"
_DEFAULT_DESC = "Audit feed"


def _parses(path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            ET.fromstring(f.read())
        return True
    except (ET.ParseError, OSError):
        return False


def _channel_meta(preamble, title, link, desc):
    """Prefer explicit args; else recover from the (possibly broken) preamble;
    else fall back to defaults — so the rebuilt header is always well-formed."""
    def recover(rx, given, default):
        if given is not None:
            return given
        m = rx.search(preamble or "")
        return xml_unescape(m.group(1).strip()) if m else default
    return (
        recover(_CH_TITLE_RE, title, _DEFAULT_TITLE),
        recover(_CH_LINK_RE, link, _DEFAULT_LINK),
        recover(_CH_DESC_RE, desc, _DEFAULT_DESC),
    )


def _reescape_item(block):
    """Rebuild one <item> block with fully-escaped fields (drops malformed
    markup inside the fields). Returns None if title/link/guid can't be found."""
    def grab(rx):
        m = rx.search(block)
        return xml_unescape(m.group(1).strip()) if m else None
    title = grab(_IT_TITLE_RE)
    link = grab(_IT_LINK_RE)
    guid = grab(_IT_GUID_RE)
    pub = grab(_IT_PUB_RE) or ""
    if title is None or link is None or guid is None:
        return None
    return _build_item(title, link, guid, pub)


def _feed_header(title, link, desc):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "<channel>\n"
        f"<title>{xml_escape(title)}</title>\n"
        f"<link>{xml_escape(link)}</link>\n"
        f"<description>{xml_escape(desc)}</description>\n"
        "<language>en-us</language>\n"
        f'<lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %Z")}</lastBuildDate>\n'
    )


def _build_item(title, link, guid, pubdate_iso):
    return (
        "<item>\n"
        f"  <title>{xml_escape(title)}</title>\n"
        f"  <link>{xml_escape(link)}</link>\n"
        f'  <guid isPermaLink="false">{xml_escape(guid)}</guid>\n'
        f"  <pubDate>{xml_escape(pubdate_iso)}</pubDate>\n"
        "</item>\n"
    )


def _read_preamble(xml):
    """Everything before the first <item> (or before </channel> if none)."""
    idx = xml.find("<item>")
    if idx == -1:
        idx = xml.find("</channel>")
        if idx == -1:
            idx = len(xml)
    return xml[:idx]


def _read_feed(path):
    """Return (preamble, items) — self-healing legacy unescaped ampersands."""
    if not os.path.exists(path):
        return None, []
    with open(path, "r", encoding="utf-8") as f:
        xml = f.read()
    xml = _STRAY_AMP_RE.sub("&amp;", xml)  # repair legacy malformed content in-place
    preamble = _read_preamble(xml)
    return preamble, [
        m.group(0) if m.group(0).endswith("\n") else m.group(0) + "\n"
        for m in _ITEM_RE.finditer(xml)
    ]


def _write_feed(path, preamble, items, max_items):
    if max_items and len(items) > max_items:
        items = items[-max_items:]  # keep the most-recent N
    with open(path, "w", encoding="utf-8") as f:
        f.write(preamble)
        f.writelines(items)
        f.write("</channel>\n</rss>\n")


def ensure_exists(path, title, link, desc):
    if os.path.exists(path):
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _write_feed(path, _feed_header(title, link, desc), [], max_items=0)


def normalize_if_needed(path, *, max_items=200, title=None, link=None, desc=None):
    """Heal/trim the feed once when it's actually broken or oversized.

    Rewrites only when needed so callers don't churn a commit every run. When a
    rewrite is needed the header is ALWAYS rebuilt from `_feed_header()` (a
    malformed preamble is never preserved), and the result is validated with
    xml.etree: if it still doesn't parse, every item is re-escaped/rebuilt and
    revalidated. Returns True only when the on-disk feed is actually well-formed
    after the rewrite; returns False if it was already fine OR is unrecoverable
    (never True-but-broken).
    """
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    needs = bool(_STRAY_AMP_RE.search(raw))
    if not needs:
        try:
            ET.fromstring(raw)
        except ET.ParseError:
            needs = True
    preamble, items = _read_feed(path)  # heals stray '&' in-memory
    if not needs and max_items and len(items) > max_items:
        needs = True
    if not needs:
        return False

    # Rebuild the header from a known-good template so a broken preamble/channel
    # tag can never survive the heal.
    header = _feed_header(*_channel_meta(preamble, title, link, desc))
    _write_feed(path, header, items, max_items)
    if _parses(path):
        return True

    # Item content itself is still malformed — re-escape/rebuild each item and,
    # as a last resort, drop any item that can't be reconstructed.
    safe_items = [b for b in (_reescape_item(it) for it in items) if b]
    _write_feed(path, header, safe_items, max_items)
    if _parses(path):
        return True

    # Unrecoverable: leave a well-formed (empty-item) feed rather than a broken
    # one, and report failure so the caller doesn't claim a successful heal.
    _write_feed(path, header, [], max_items)
    return False


def append_item(path, title, link, guid, pubdate, *, max_items=200):
    """Append one escaped item, rebuilding the feed (self-heals + trims to max_items)."""
    preamble, items = _read_feed(path)
    items.append(_build_item(title, link, guid, pubdate))
    _write_feed(path, preamble, items, max_items)
