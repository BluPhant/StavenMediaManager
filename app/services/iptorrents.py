"""
IPTorrents client — search via RSS feed, fetch .torrent files for loading into rTorrent.

No external dependencies — uses only Python stdlib (urllib, xml.etree).

Credentials (all via environment variables):
  IPTORRENTS_USER_ID  — numeric user ID from your IPT profile page
  IPTORRENTS_PASSKEY  — passkey / API key from your IPT profile page
  IPTORRENTS_DOMAIN   — optional domain override (default: iptorrents.com)

No credentials are stored in this file.
"""
import gzip
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


# ── Query parser ─────────────────────────────────────────────────────────────

# Year: a 4-digit number in the range 1900–2099
_YEAR_RE = re.compile(r"^(19\d{2}|20[0-2]\d)$")

# Known release-metadata tokens (case-insensitive explicit list — no catch-all)
_KNOWN_RELEASE_RE = re.compile(
    r"^("
    r"\d{3,4}[pP]"                          # 720p 1080p 2160p
    r"|4[Kk]|UHD"                            # 4K UHD
    r"|HDR10?\+?|SDR|DV|IMAX|DOLBY"
    r"|WEB[-.]?DL|WEBRip|WEBDL"
    r"|AMZN|NF|DSNP|HULU|PCOK|ATVP|PMTP|MAX|SHO|TUBI"
    r"|BluRay|BDRip|BRRip|Blu[-.]Ray|REMUX"
    r"|DVDRip|DVDSCR|HDTV|PDTV"
    r"|x26[45]|H\.?26[45]|HEVC|AVC"
    r"|AAC|DDP?5?\.?[01]?|DTS[-A-Z]*|FLAC|TrueHD|Atmos|DD[25]"
    r"|PROPER|REPACK|RERIP|INTERNAL|LIMITED|EXTENDED|UNRATED|DIRECTORS"
    r"|SUBBED|DUBBED|MULTI|FRENCH|GERMAN|SPANISH|ITALIAN|HINDI"
    r"|S\d{2}E?\d*|E\d{2}"                  # season/episode markers S01 S01E02 E03
    r")$",
    re.IGNORECASE,
)


def _is_release_group(tok: str) -> bool:
    """True if the token looks like an all-caps release group name (e.g. BYNDR, LAMA)."""
    # Must be fully uppercase letters/digits, at least 2 chars, no lowercase
    return bool(re.match(r"^[A-Z0-9]{2,12}$", tok)) and tok == tok.upper()


def parse_query(q: str) -> dict:
    """
    Split a freeform torrent search string into (title, year, extras).

    'Brazil 1944 LAMA'               → title='Brazil',          year='1944', extras=['1944','LAMA']
    'Hoppers 2026 2160p WEB-DL BYNDR'→ title='Hoppers',         year='2026', extras=[...]
    'The Bear S03E01'                 → title='The Bear',        year=None,   extras=['S03E01']
    'A Love Like This 2026 1080p'     → title='A Love Like This',year='2026', extras=[...]
    """
    tokens = q.strip().split()
    year = None
    title_tokens: list[str] = []
    extra_tokens: list[str] = []
    past_title = False

    for tok in tokens:
        if _YEAR_RE.match(tok):
            year = tok
            past_title = True
            extra_tokens.append(tok)
        elif _KNOWN_RELEASE_RE.match(tok):
            # Known codec/resolution/service/episode tag — always signals metadata
            past_title = True
            extra_tokens.append(tok)
        elif past_title and _is_release_group(tok):
            # All-caps token AFTER we've already passed the title section
            extra_tokens.append(tok)
        elif not past_title:
            title_tokens.append(tok)
        else:
            # Past title but not a known release token — still treat as extra
            extra_tokens.append(tok)

    return {
        "title":  " ".join(title_tokens),
        "year":   year,
        "extras": extra_tokens,
        "raw":    q.strip(),
    }


def build_search_cascade(q: str) -> tuple[list[str], str | None]:
    """
    Return (ordered_query_list, detected_year).
    The list goes from most-specific to broadest; try each until results appear.

    Example for 'Brazil 1944 LAMA':
      ['Brazil 1944 LAMA', 'Brazil 1944', 'Brazil']   year=None
      (1944 is treated as year only if it looks like a plausible release year
       that follows the title — otherwise kept as part of the extras list)
    """
    parsed = parse_query(q)
    title = parsed["title"]
    year  = parsed["year"]

    candidates: list[str] = []

    # 1. Full original query
    if q.strip():
        candidates.append(q.strip())

    # 2. Title + year (without other extras)
    if title and year:
        ty = f"{title} {year}"
        if ty not in candidates:
            candidates.append(ty)

    # 3. Title only
    if title and title not in candidates:
        candidates.append(title)

    # 4. Progressive word-drop on the title (right to left)
    title_words = title.split()
    for n in range(len(title_words) - 1, 0, -1):
        partial = " ".join(title_words[:n])
        if partial and partial not in candidates:
            candidates.append(partial)

    return candidates, year


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
        # IPTorrents sends gzip-compressed responses
        if data[:2] == b'\x1f\x8b':
            data = gzip.decompress(data)
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

        # Parse all items first — we filter by title below, so cutting at
        # `limit` before filtering could drop genuine matches.
        for item in items:
            try:
                result = self._parse_item(item)
                if result:
                    results.append(result)
            except Exception as exc:
                logger.debug(f"IPT RSS item parse error: {exc}")
                continue

        # IPT's q= parameter searches description text as well as titles, so
        # we apply a client-side title filter to strip false positives.
        if query:
            words = [w.lower() for w in query.split() if w]
            results = [r for r in results if _title_matches(r.title, words)]

        logger.info(f"IPT search returned {len(results)} results (after title filter)")
        return results[:limit]

    def _parse_item(self, item: ET.Element) -> IPTResult | None:
        # ── Title ────────────────────────────────────────────────────────────
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            return None

        # ── Info URL (guid = details page, link = download URL on IPT) ───────
        guid_el = item.find("guid")
        info_url = (guid_el.text or "").strip() if guid_el is not None else ""
        torrent_id = _extract_id(info_url) or title[:40]

        # ── Enclosure: .torrent URL + file size ───────────────────────────────
        # IPT does not percent-encode spaces in the URL — fix that here.
        enclosure = item.find("enclosure")
        torrent_url = ""
        size_bytes = 0
        if enclosure is not None:
            raw_url = enclosure.get("url", "")
            # Encode spaces in path segment only (preserve query string)
            path_part, _, query_part = raw_url.partition("?")
            torrent_url = path_part.replace(" ", "%20")
            if query_part:
                torrent_url += "?" + query_part
            try:
                size_bytes = int(enclosure.get("length", 0))
            except (ValueError, TypeError):
                size_bytes = 0

        if not torrent_url:
            return None

        # ── Description: "{size}; {Category/Sub} (S:{n} L:{n})" ─────────────
        desc_el = item.find("description")
        desc = (desc_el.text or "") if desc_el is not None else ""

        # Category — text between "; " and " ("
        ipt_category = ""
        cat_m = re.search(r";\s*(.+?)\s*(?:\(S:|$)", desc)
        if cat_m:
            ipt_category = cat_m.group(1).strip()

        # Seeds / Leechers — "(S:N L:N)"
        seeders  = _parse_int_from_text(desc, r"S:(\d+)")
        leechers = _parse_int_from_text(desc, r"L:(\d+)")

        # Size fallback from description if enclosure length was missing
        if size_bytes == 0:
            size_bytes = _parse_size_from_text(desc)

        suggested_type = _guess_type_from_ipt_category(ipt_category)

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

    def smart_search(self, q: str, category: str = "all",
                     limit: int = 50) -> dict:
        """
        Progressive search: tries increasingly broad queries until results appear.

        Returns:
          {
            results:       list[IPTResult],
            query_used:    str | None,        # the query that produced results
            year:          str | None,        # detected year, if any
            attempts:      list[str],         # every query tried (incl. successful)
          }
        """
        if not self.is_configured():
            raise RuntimeError("IPTorrents not configured.")

        cascade, year = build_search_cascade(q)
        attempts: list[str] = []

        for query in cascade:
            results = self.search(query=query, category=category, limit=limit)
            attempts.append(query)
            if results:
                # If a year was detected, float results whose title contains it
                if year:
                    results.sort(key=lambda r: (year not in r.title, r.title))
                return {
                    "results":    results,
                    "query_used": query,
                    "year":       year,
                    "attempts":   attempts,
                }

        return {
            "results":    [],
            "query_used": None,
            "year":       year,
            "attempts":   attempts,
        }

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


def _title_matches(title: str, words: list[str]) -> bool:
    """Return True if every word in *words* appears in *title* (case-insensitive)."""
    tl = title.lower()
    return all(w in tl for w in words)


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
