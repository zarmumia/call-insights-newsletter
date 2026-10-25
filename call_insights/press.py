"""Press scan: read RSS/Atom feeds from the client's field and pick recent, relevant items.

We keep only the title, link and a short excerpt. The excerpt is input for the
writer, never published: the newsletter links to the original article and says
in its own words why it matters.
"""
from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class PressItem:
    title: str
    link: str
    source: str
    published: datetime | None
    excerpt: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "link": self.link, "source": self.source,
                "published": self.published.isoformat() if self.published else None}


def _clean(text: str | None, limit: int = 300) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)          # RSS: RFC 822
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom: ISO 8601
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(xml_bytes: bytes, source: str) -> list[PressItem]:
    root = ET.fromstring(xml_bytes)
    items = []
    channel_title = root.findtext("channel/title") or root.findtext(f"{ATOM}title") or source
    for it in root.iter("item"):                                   # RSS 2.0
        items.append(PressItem(_clean(it.findtext("title"), 200), (it.findtext("link") or "").strip(),
                               channel_title, _date(it.findtext("pubDate")), _clean(it.findtext("description"))))
    for e in root.iter(f"{ATOM}entry"):                            # Atom
        link_el = e.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        items.append(PressItem(_clean(e.findtext(f"{ATOM}title"), 200), link, channel_title,
                               _date(e.findtext(f"{ATOM}updated") or e.findtext(f"{ATOM}published")),
                               _clean(e.findtext(f"{ATOM}summary"))))
    return [i for i in items if i.title and i.link.startswith("http")]


def read_source(src: str, base: Path) -> bytes:
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": "call-insights-newsletter/0.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    return (base / src).read_bytes()


def scan(feeds_file: str | Path, keywords: list[str], now: datetime, days: int = 30,
         limit: int = 5) -> tuple[list[PressItem], list[str]]:
    """Return relevant recent items and a list of feeds that failed (a broken feed never stops the run)."""
    path = Path(feeds_file)
    sources = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.startswith("#")]
    since, errors, items = now - timedelta(days=days), [], []
    for src in sources:
        try:
            items += parse_feed(read_source(src, path.parent), src)
        except Exception as e:  # noqa: BLE001 - report and move on
            errors.append(f"{src}: {e}")
    kw = [k.lower() for k in keywords]
    picked = [i for i in items
              if (i.published is None or i.published >= since)
              and (not kw or any(k in f"{i.title} {i.excerpt}".lower() for k in kw))]
    seen, unique = set(), []
    for i in sorted(picked, key=lambda i: i.published or since, reverse=True):
        if i.link not in seen:
            seen.add(i.link)
            unique.append(i)
    return unique[:limit], errors
