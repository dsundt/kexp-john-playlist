import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom

from kexp.feed import ensure_exists, append_item, normalize_if_needed

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<rss version="2.0">\n'
    "<channel>\n"
    "<title>Test Feed</title>\n"
    "<link>http://example.com</link>\n"
    "<description>desc</description>\n"
    "<language>en-us</language>\n"
    "<lastBuildDate>Thu, 01 Jan 2026 00:00:00 UTC</lastBuildDate>\n"
)


def _make_malformed_feed(path, n_items):
    items = []
    for i in range(n_items):
        # Item 2 has a raw, unescaped '&' — the legacy malformed case.
        title = "Rock & Roll" if i == 2 else f"Artist {i} — Song {i}"
        items.append(
            "<item>\n"
            f"  <title>{title}</title>\n"
            f"  <link>http://x/{i}</link>\n"
            f'  <guid isPermaLink="false">g{i}</guid>\n'
            f"  <pubDate>2026-01-0{i + 1}T00:00:00+00:00</pubDate>\n"
            "</item>\n"
        )
    content = _HEADER + "".join(items) + "</channel>\n</rss>\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_normalize_heals_stray_amp_and_trims_then_idempotent(tmp_path):
    path = str(tmp_path / "feed.xml")
    _make_malformed_feed(path, 5)

    # Malformed (stray '&') + over max_items=3 -> must heal and trim.
    changed = normalize_if_needed(path, max_items=3)
    assert changed is True

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    # Now well-formed XML.
    root = ET.fromstring(raw)
    items = root.findall("./channel/item")
    assert len(items) == 3

    # Second call is a no-op (idempotent) — no stray amps, already trimmed.
    changed_again = normalize_if_needed(path, max_items=3)
    assert changed_again is False


def test_append_item_escapes_and_roundtrips(tmp_path):
    path = str(tmp_path / "feed.xml")
    ensure_exists(path, "Feed Title", "http://example.com", "desc")

    append_item(
        path,
        title="Simon & Garfunkel — <Live>",
        link="http://x/1",
        guid="g1",
        pubdate="2026-01-01T00:00:00+00:00",
        max_items=200,
    )

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    dom = minidom.parseString(raw)  # raises ExpatError if not well-formed
    titles = dom.getElementsByTagName("title")
    # titles[0] is the channel title; titles[1] is the item title.
    item_title = titles[1].firstChild.data
    assert item_title == "Simon & Garfunkel — <Live>"
