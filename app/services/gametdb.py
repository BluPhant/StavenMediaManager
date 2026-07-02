"""
GameTDB client — scrapes https://www.gametdb.com/Switch/{ID} for game metadata.

GameTDB has no public search API.  Lookup is by 5-char game ID (e.g. BFLTA).
IDs are often embedded in scene release filenames: hr-bflta.xci → BFLTA.
Cover art is served at https://art.gametdb.com/switch/cover/{REGION}/{ID}.jpg
"""
import logging
import re
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_COVER_REGIONS = ["US", "EN", "AU", "CA"]
_GAME_PAGE_URL = "https://www.gametdb.com/Switch/{id}"
_COVER_URL     = "https://art.gametdb.com/switch/cover/{region}/{id}.jpg"

# Scene filenames: {group}-{ID}.{ext}  e.g. hr-bflta.xci
# The ID segment is 4-6 alphanumeric chars (GameTDB IDs); longer segments are game names.
_SCENE_ID_RE = re.compile(r"^[a-z0-9]+-([a-z0-9]{4,6})(?:_v[\d.]+)?$", re.IGNORECASE)

# Torrent folder patterns:
#   GameTitle_NSW-GROUP           → base
#   GameTitle_eShop_NSW-GROUP     → base (eShop)
#   GameTitle_Update_v1.0.3_NSW   → update
#   GameTitle_DLC_Name_NSW        → dlc
_CONTENT_UPDATE_RE = re.compile(r"[_\s]Update[_\s]v?([\d.]+)", re.IGNORECASE)
_CONTENT_DLC_RE    = re.compile(r"[_\s]DLC[_\s]?(.*?)[_\s]NSW", re.IGNORECASE)
_NSW_SUFFIX_RE     = re.compile(r"[_\s](?:eShop[_\s])?NSW(?:[_-].*)?$", re.IGNORECASE)


@dataclass
class GameTDBGame:
    game_id:   str
    title:     str
    developer: str = ""
    publisher: str = ""
    cover_url: str = ""


def extract_id_from_filename(filename: str) -> str | None:
    """
    Try to extract a GameTDB-style ID from a scene release filename.
    e.g. hr-bflta.xci → BFLTA
    Returns None if the filename doesn't match the expected pattern.
    """
    stem = re.sub(r"\.[a-z0-9]{2,4}$", "", filename, flags=re.IGNORECASE)
    m = _SCENE_ID_RE.match(stem)
    if m:
        candidate = m.group(1).upper()
        if len(candidate) >= 4:
            return candidate
    return None


def parse_content_type(folder_name: str) -> tuple[str, str | None, str | None]:
    """
    Inspect a torrent folder name and return (content_type, version, dlc_name).
    content_type: 'base' | 'update' | 'dlc'
    """
    m_update = _CONTENT_UPDATE_RE.search(folder_name)
    if m_update:
        return "update", m_update.group(1), None

    m_dlc = _CONTENT_DLC_RE.search(folder_name)
    if m_dlc:
        dlc_name = m_dlc.group(1).replace("_", " ").strip() or None
        return "dlc", None, dlc_name

    return "base", None, None


def clean_title_from_folder(folder_name: str) -> str:
    """
    Derive a human-readable title from a torrent folder name.
    Rhythm_Heaven_Groove_NSW-HR → Rhythm Heaven Groove
    """
    title = _NSW_SUFFIX_RE.sub("", folder_name)
    title = _CONTENT_UPDATE_RE.sub("", title)
    title = _CONTENT_DLC_RE.sub("", title)
    title = title.replace("_", " ").strip()
    return title


def get_game(game_id: str) -> GameTDBGame | None:
    """
    Fetch metadata for a Switch game from GameTDB by its 5-char ID.
    Returns None if the game is not found or the request fails.
    """
    game_id = game_id.strip().upper()
    url = _GAME_PAGE_URL.format(id=game_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StavenMediaManager/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning(f"GameTDB fetch failed for {game_id}: {exc}")
        return None

    # Check it's a real game page (not a 404 redirect)
    if f"/{game_id}" not in html and game_id not in html:
        logger.warning(f"GameTDB: no page found for {game_id}")
        return None

    title     = _parse_field(html, "title (EN)") or _parse_title_tag(html, game_id)
    developer = _parse_field(html, "developer") or ""
    publisher = _parse_field(html, "publisher") or ""

    if not title:
        logger.warning(f"GameTDB: could not parse title for {game_id}")
        return None

    cover_url = _best_cover_url(game_id, html)

    return GameTDBGame(
        game_id=game_id,
        title=title,
        developer=developer,
        publisher=publisher,
        cover_url=cover_url,
    )


def fetch_cover_bytes(game_id: str) -> bytes | None:
    """Download cover art for a game. Tries US, then EN, then AU regions."""
    game_id = game_id.strip().upper()
    for region in _COVER_REGIONS:
        url = _COVER_URL.format(region=region, id=game_id)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "StavenMediaManager/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception:
            pass
    return None


# ── HTML parsing helpers ──────────────────────────────────────────────────────

def _parse_field(html: str, field_name: str) -> str | None:
    """Extract a table cell value from the GameTDB game page."""
    pattern = re.compile(
        re.escape(field_name) + r'</td><td[^>]*>([^<]+)</td>',
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if m:
        return m.group(1).strip() or None
    return None


def _parse_title_tag(html: str, game_id: str) -> str | None:
    """Fallback: extract title from <title>BFLTA - Rhythm Heaven Groove</title>."""
    m = re.search(r"<title>[^<]*" + re.escape(game_id) + r"\s*-\s*([^<]+)</title>",
                  html, re.IGNORECASE)
    if m:
        return m.group(1).strip() or None
    return None


def _best_cover_url(game_id: str, html: str) -> str:
    """Return the best available cover art URL (prefer US, fall back via regions)."""
    for region in _COVER_REGIONS:
        url = _COVER_URL.format(region=region, id=game_id)
        if url in html:
            return url
    return _COVER_URL.format(region="US", id=game_id)
