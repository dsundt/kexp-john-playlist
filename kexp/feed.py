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
from xml.sax.saxutils import escape as xml_escape

# Match one <item>...</item> block (used to parse/trim the feed).
_ITEM_RE = re.compile(r"[ \t]*<item>.*?</item>\n?", re.DOTALL)
# Match a bare & that is NOT already the start of a valid entity (legacy self-heal).
_STRAY_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)")


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


def normalize_if_needed(path, *, max_items=200):
    """Heal/trim the feed once when it's actually broken or oversized.

    Rewrites only when needed so callers don't churn a commit every run.
    Returns True iff the file was rewritten.
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
    if needs:
        _write_feed(path, preamble, items, max_items)
    return needs


def append_item(path, title, link, guid, pubdate, *, max_items=200):
    """Append one escaped item, rebuilding the feed (self-heals + trims to max_items)."""
    preamble, items = _read_feed(path)
    items.append(_build_item(title, link, guid, pubdate))
    _write_feed(path, preamble, items, max_items)
