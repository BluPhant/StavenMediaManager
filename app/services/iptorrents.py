"""
IPTorrents client — search via RSS feed, fetch .torrent files for loading into rTorrent.

No external dependencies — uses only Python stdlib (urllib, xml.etree).

Credentials (all via environment variables):
  IPTORRENTS_USER_ID  — numeric user ID from your IPT profile page
  IPTORRENTS_PASSKEY  — passkey / API key from your IPT profile page
  IPTORRENTS_DOMAIN   — optional domain override (default: iptorrents.com)

No credentials are stored in this file.
"""
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ..config import settings

logger = logging.getLogger(__name__)

# ── Category definitions ──────────────────────────────────────────────────────

# Friendly name → list of IPTorrents numeric category IDs
# IDs sourced from IPTorrents RSS documentation and community wikis.
CATEGORY_IDS: dict[str, list[int]] = {
    "movies":     [72, 87, 77, 101, 48, 54, 89, 95, 96, 98, 99, 100],
    "tv":         [73, 74, 78, 66, 82, 79, 100, 103, 104],
    "music":      [22, 80, 31, 46, 56, 97],
    "audiobooks": [16],
    "games":      [7, 8, 42, 43, 57, 59, 60, 64],
    "ebooks":     [55],
    "software":   [33, 52],
}

# IPTorrents category string → our standard type
CATEGORY_TYPE_MAP: dict[str, str] = {
    "movie":      "movies",
    "movies":     "movies",
    "tv":         "tv",
    "television": "tv",
    "music":      "music",
    "audiobook":  "audiobooks",
    "ebook":      "ebooks",
    "games":      "games",
    "software":   "software",
}


def _guess_type_from_ipt_category(cat_str: str) -> str:
    """Map an IPTorrents category string like 'Movie/4K' to our type."""
    lower = cat_str.lower()
    for key, typ in CATEGORY_TYPE_MAP.items():
        if key in lower:
            return typ
    return "_unsorted"


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class IPTResult:
    torrent_id: str
    title: str
    size_bytes: int
    seeders: int
    leechers: int
    ipt_category: str        # raw IPT category string, e.g. "Movie/4K"
    suggested_type: str      # our normalised type: movies, tv, music, …
    torrent_url: str         # direct .torrent download URL (auth embedded)
    info_url: str            # IPTorrents details page URL
    pubdate: str = ""
    extra: dict = field(default_factory=dict)


# ── RSS feed client ───────────────────────────────────────────────────────────

class IPTorrentsClient:

    def is_configured(self) -> bool:
        return bool(settings.iptorrents_user_id and settings.iptorrents_passkey)

    def _rss_url(self, query: str = "", category: str = "all") -> str:
        domain = settings.iptorrents_domain or "iptorrents.com"
        uid = settings.iptorrents_user_id
        key = settings.iptorrents_passkey

        url = f"https://{domain}/t.rss?u={uid};tp={key}"

        if category != "all":
            ids = CATEGORY_IDS.get(category, [])
            for cid in ids:
                url += f";{cid}"

        if query:
            url += f";q={urllib.parse.quote(query.strip())}"

        url += ";download"   # embed auth into enclosure URLs
        return url

    def _fetch_xml(self, url: str, timeout: int = 20) -> ET.Element:
        req = urllib.request.Request(url, headers={"User-Agent": "StavenMediaManager/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return ET.fromstring(data)

    def search(self, query: str = "", category: str = "all",
               limit: int = 50) -> list[IPTResult]:
        """
        Search IPTorrents via RSS.

        query     — title text to filter (empty → most recent for the category)
        category  — one of: all, movies, tv, music, audiobooks, games, ebooks, software
        limit     — max results to return
        """
        if not self.is_configured():
            raise RuntimeError("IPTorrents not configured (IPTORRENTS_USER_ID / IPTORRENTS_PASSKEY missing).")

        url = self._rss_url(query=query, category=category)
        logger.info(f"IPT RSS search: query={query!r} category={category} limit={limit}")

        try:
            root = self._fetch_xml(url)
        except Exception as exc:
            raise RuntimeError(f"IPTorrents RSS fetch failed: {exc}") from exc

        channel = root.find("channel")
        if channel is None:
            return []

        items = channel.findall("item")
        results: list[IPTResult] = []

        for item in items[:limit]:
            try:
                result = self._parse_item(item)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.debug(f"IPT RSS item parse error: {exc}")
                continue

        logger.info(f"IPT search returned {len(results)} results")
        return results

    def _parse_item(self, item: ET.Element) -> IPTResult | None:
        # ── Title ────────────────────────────────────────────────────────────
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            return None

        # ── Links ────────────────────────────────────────────────────────────
        link_el = item.find("link")
        info_url = (link_el.text or "").strip() if link_el is not None else ""

        # Enclosure carries the .torrent URL + file size
        enclosure = item.find("enclosure")
        torrent_url = ""
        size_bytes = 0
        if enclosure is not None:
            torrent_url = enclosure.get("url", "")
            try:
                size_bytes = int(enclosure.get("length", 0))
            except (ValueError, TypeError):
                size_bytes = 0

        if not torrent_url:
            return None

        # ── Torrent ID (from URL or link) ─────────────────────────────────
        torrent_id = _extract_id(torrent_url) or _extract_id(info_url) or title[:40]

        # ── Category ─────────────────────────────────────────────────────────
        cat_el = item.find("category")
        ipt_category = (cat_el.text or "").strip() if cat_el is not None else ""
        suggested_type = _guess_type_from_ipt_category(ipt_category)

        # ── Seeds / Leechers (from description text) ──────────────────────
        desc_el = item.find("description")
        desc = desc_el.text or "" if desc_el is not None else ""
        seeders = _parse_int_from_text(desc, r"[Ss]eeders?[:\s]+(\d+)") or \
                  _parse_int_from_text(desc, r"[Ss]eeds[:\s]+(\d+)") or 0
        leechers = _parse_int_from_text(desc, r"[Ll]eechers?[:\s]+(\d+)") or \
                   _parse_int_from_text(desc, r"[Ll]eeches[:\s]+(\d+)") or 0

        # Also try to extract size from description if enclosure length was 0
        if size_bytes == 0:
            size_bytes = _parse_size_from_text(desc)

        # ── Pub date ─────────────────────────────────────────────────────────
        pubdate_el = item.find("pubDate")
        pubdate = (pubdate_el.text or "").strip() if pubdate_el is not None else ""

        return IPTResult(
            torrent_id=torrent_id,
            title=title,
            size_bytes=size_bytes,
            seeders=seeders,
            leechers=leechers,
            ipt_category=ipt_category,
            suggested_type=suggested_type,
            torrent_url=torrent_url,
            info_url=info_url,
            pubdate=pubdate,
        )

    def fetch_torrent_bytes(self, torrent_url: str) -> bytes:
        """Download the .torrent file from IPTorrents and return raw bytes."""
        req = urllib.request.Request(
            torrent_url,
            headers={"User-Agent": "StavenMediaManager/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except Exception as exc:
            raise RuntimeError(f"Failed to download .torrent: {exc}") from exc

        if len(data) < 10:
            raise RuntimeError(f"Downloaded .torrent is suspiciously small ({len(data)} bytes) — auth may have failed.")
        if not data.startswith(b"d8:") and not data.startswith(b"d"):
            # Not a valid bencoded torrent — probably an HTML error page
            snippet = data[:200].decode(errors="replace")
            raise RuntimeError(f"Response does not look like a .torrent file: {snippet!r}")

        return data


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_id(url: str) -> str:
    """Try to pull a numeric torrent ID out of an IPTorrents URL."""
    m = re.search(r"/(?:t|download\.php)/(\d+)", url)
    return m.group(1) if m else ""


def _parse_int_from_text(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    try:
        return int(m.group(1)) if m else 0
    except (ValueError, AttributeError):
        return 0


def _parse_size_from_text(text: str) -> int:
    """Try to parse a human-readable size like '18.2 GB' from description text."""
    m = re.search(r"([\d,.]+)\s*(TB|GB|MB|KB|B)\b", text, re.IGNORECASE)
    if not m:
        return 0
    try:
        value = float(m.group(1).replace(",", ""))
        unit = m.group(2).upper()
        multipliers = {"TB": 1 << 40, "GB": 1 << 30, "MB": 1 << 20, "KB": 1 << 10, "B": 1}
        return int(value * multipliers.get(unit, 1))
    except (ValueError, KeyError):
        return 0
