"""
Plex library service — query the Plex API for movie library status.

Handles:
- Section ID discovery and caching
- Full library scan with resolution and IMDB ID extraction
- 60-second in-memory cache so confirm() calls are fast
- Targeted path refresh after a move (non-blocking)
"""
import json
import logging
import re
import time
import urllib.parse
import urllib.request

from ..config import settings

logger = logging.getLogger(__name__)

# Resolution string → tier rank (higher = better)
RESOLUTION_RANK: dict[str, int] = {
    "4k": 4, "2160": 4,
    "1440": 3,
    "1080": 2,
    "720": 1,
    "480": 0,
}
TARGET_RANK = 4  # 2160p / 4K

_movie_section_id: str | None = None   # cached after first successful lookup
_library_cache:   dict | None = None   # {imdb_id: {...}}
_library_cache_at: float = 0.0
_CACHE_TTL = 60.0  # seconds


# ── Public helpers ────────────────────────────────────────────────────────────

def check_movie(imdb_id: str) -> dict:
    """
    Return Plex status for a single movie by IMDB ID.
    {found, resolution, resolution_rank, path, size_bytes}
    """
    lib = _get_library()
    entry = lib.get(imdb_id)
    if not entry:
        return {"found": False, "resolution": None, "resolution_rank": -1, "path": None, "size_bytes": None}
    res = entry.get("resolution") or ""
    rank = RESOLUTION_RANK.get(str(res).lower(), 0)
    return {
        "found": True,
        "resolution": res,
        "resolution_rank": rank,
        "path": entry.get("plex_path"),
        "size_bytes": entry.get("size_bytes"),
    }


def needs_upgrade(imdb_id: str) -> bool:
    """True if the movie is in Plex but below 2160p."""
    info = check_movie(imdb_id)
    return info["found"] and info["resolution_rank"] < TARGET_RANK


def refresh_library_path(path: str | None = None) -> None:
    """
    Trigger a Plex library scan.  If path is given, uses targeted refresh.
    Non-blocking — caller does not wait for the scan to complete.
    Invalidates the in-memory cache so the next check re-fetches.
    """
    global _library_cache_at
    if not (settings.plex_url and settings.plex_token):
        return
    sid = _get_section_id()
    base  = settings.plex_url.rstrip("/")
    token = settings.plex_token
    if path and sid:
        url = f"{base}/library/sections/{sid}/refresh?path={urllib.parse.quote(path)}&X-Plex-Token={token}"
    elif sid:
        url = f"{base}/library/sections/{sid}/refresh?X-Plex-Token={token}"
    else:
        url = f"{base}/library/sections/all/refresh?X-Plex-Token={token}"
    try:
        urllib.request.urlopen(urllib.request.Request(url), timeout=10)  # noqa: S310
        _library_cache_at = 0.0   # force re-fetch on next check
        logger.info(f"Plex refresh triggered (path={path!r})")
    except Exception as exc:
        logger.warning(f"Plex refresh failed (non-fatal): {exc}")


def get_section_id_for_movies() -> str | None:
    """Return the cached Plex movie section ID (calls API once if not cached)."""
    return _get_section_id()


# ── Internal ──────────────────────────────────────────────────────────────────

def _get_section_id() -> str | None:
    global _movie_section_id
    if _movie_section_id is not None:
        return _movie_section_id
    if not (settings.plex_url and settings.plex_token):
        return None
    try:
        url = f"{settings.plex_url.rstrip('/')}/library/sections?X-Plex-Token={settings.plex_token}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
        for section in data.get("MediaContainer", {}).get("Directory", []):
            if section.get("type") == "movie":
                _movie_section_id = str(section["key"])
                logger.info(f"Plex movie section ID cached: {_movie_section_id}")
                return _movie_section_id
    except Exception as exc:
        logger.warning(f"Plex section lookup failed: {exc}")
    return None


def _get_library() -> dict[str, dict]:
    """Return full movie library dict (cached 60s).  {imdb_id → metadata}"""
    global _library_cache, _library_cache_at
    now = time.monotonic()
    if _library_cache is not None and (now - _library_cache_at) < _CACHE_TTL:
        return _library_cache

    if not (settings.plex_url and settings.plex_token):
        return {}

    sid = _get_section_id()
    if not sid:
        return {}

    try:
        url = (
            f"{settings.plex_url.rstrip('/')}/library/sections/{sid}/all"
            f"?type=1&X-Plex-Token={settings.plex_token}"
        )
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())

        lib: dict[str, dict] = {}
        for item in data.get("MediaContainer", {}).get("Metadata", []):
            imdb_id = _extract_imdb_id(item)
            if not imdb_id:
                continue
            resolution = plex_path = size_bytes = None
            for media in item.get("Media", []):
                resolution = media.get("videoResolution")
                for part in media.get("Part", []):
                    plex_path  = part.get("file")
                    size_bytes = part.get("size")
                    break
                break
            lib[imdb_id] = {
                "title":      item.get("title", ""),
                "year":       item.get("year"),
                "resolution": resolution,
                "plex_path":  plex_path,
                "size_bytes": size_bytes,
                "rating_key": item.get("ratingKey"),
            }

        _library_cache    = lib
        _library_cache_at = now
        logger.info(f"Plex library cached: {len(lib)} movies")
        return lib

    except Exception as exc:
        logger.warning(f"Plex library fetch failed: {exc}")
        return _library_cache or {}


def _extract_imdb_id(item: dict) -> str | None:
    """
    Extract IMDB ID from a Plex metadata item.
    Handles both new-style (Guid array) and old-style (guid attribute) agents.
    """
    # New Plex (Plex Movie agent): Guid is a list of {id: "imdb://ttNNNNNN"}
    for g in item.get("Guid", []):
        gid = g.get("id", "")
        if gid.startswith("imdb://"):
            return gid[7:].split("?")[0]   # strip "imdb://" and any ?lang=... suffix

    # Old Plex agents: guid attribute like "com.plexapp.agents.imdb://tt1234567?lang=en"
    guid = item.get("guid", "")
    if "imdb://" in guid:
        m = re.search(r"imdb://(tt\d+)", guid)
        if m:
            return m.group(1)

    return None
