"""
nswdb cache — fetches and locally caches the nswdb.com/xml.php scene release database.
Used to auto-match Switch torrent folder names to clean game titles + Nintendo Title IDs.
"""
import logging
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

from ..config import settings

logger = logging.getLogger(__name__)

_NSWDB_URL = "https://nswdb.com/xml.php"
_CACHE_TTL = 86400  # 24 hours

_cache_timestamp: float = 0.0
_release_index: dict[str, dict] = {}  # normalized releasename → entry
_name_list: list[dict] = []           # all entries for name search


def _normalize_release(s: str) -> str:
    """Canonical form for a release name or torrent folder name."""
    s = s.lower()
    s = re.sub(r'[_.](?:eshop[_.])?(?:multi\d*[_.])?nsw[-_].*$', '', s)
    s = re.sub(r'[_.](?:proper|fixed|repack|dirfix|readnfo).*$', '', s)
    s = re.sub(r'[_.]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _normalize_name(s: str) -> str:
    """Canonical form for the <name> field (strips Rev tags, lowercases)."""
    s = s.lower()
    s = re.sub(r'\s*\[rev\s+[\d.]+\]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _parse_xml(data: bytes) -> None:
    global _release_index, _name_list
    root = ET.fromstring(data)
    rel_idx: dict[str, dict] = {}
    names: list[dict] = []
    for release in root.findall("release"):
        name      = release.findtext("name") or ""
        relname   = release.findtext("releasename") or ""
        titleid   = release.findtext("titleid") or ""
        publisher = release.findtext("publisher") or ""
        group     = release.findtext("group") or ""
        rtype     = release.findtext("type") or "1"
        if not name or not relname:
            continue
        entry = {
            "name": name,
            "releasename": relname,
            "titleid": titleid,
            "publisher": publisher,
            "group": group,
            "type": rtype,
        }
        norm_rel = _normalize_release(relname)
        if norm_rel and norm_rel not in rel_idx:
            rel_idx[norm_rel] = entry
        names.append({**entry, "_norm_name": _normalize_name(name)})
    _release_index = rel_idx
    _name_list = names


def _load() -> bool:
    global _cache_timestamp
    cache_path = os.path.join(settings.config_dir, "nswdb_cache.xml")
    now = time.time()

    # Use disk cache if fresh
    if os.path.exists(cache_path):
        mtime = os.path.getmtime(cache_path)
        if now - mtime < _CACHE_TTL:
            try:
                with open(cache_path, "rb") as f:
                    _parse_xml(f.read())
                _cache_timestamp = mtime
                logger.info(f"nswdb: loaded {len(_name_list)} entries from disk cache")
                return True
            except Exception as exc:
                logger.warning(f"nswdb disk cache read failed: {exc}")

    # Download
    try:
        logger.info("nswdb: downloading fresh XML…")
        req = urllib.request.Request(_NSWDB_URL, headers={"User-Agent": "StavenMediaManager/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        os.makedirs(settings.config_dir, exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(data)
        _parse_xml(data)
        _cache_timestamp = now
        logger.info(f"nswdb: cached {len(_name_list)} entries")
        return True
    except Exception as exc:
        logger.warning(f"nswdb download failed: {exc}")
        # Fall back to stale cache
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as f:
                    _parse_xml(f.read())
                logger.info(f"nswdb: using stale cache ({len(_name_list)} entries)")
                return True
            except Exception:
                pass
        return False


def _ensure_loaded() -> bool:
    if not _name_list or time.time() - _cache_timestamp > _CACHE_TTL:
        return _load()
    return True


def match_release(folder_name: str) -> dict | None:
    """
    Match a torrent folder name against nswdb release names.
    Returns {name, releasename, titleid, publisher, type} or None.
    """
    if not _ensure_loaded():
        return None
    return _release_index.get(_normalize_release(folder_name))


def search_by_name(query: str, limit: int = 10) -> list[dict]:
    """Full-text search across <name> fields. Returns list of entry dicts."""
    if not _ensure_loaded():
        return []
    q = query.lower().strip()
    if not q:
        return []
    words = q.split()
    results: list[dict] = []
    seen: set[str] = set()
    for entry in _name_list:
        norm = entry["_norm_name"]
        if all(w in norm for w in words):
            key = norm
            if key not in seen:
                seen.add(key)
                results.append({
                    "name":      entry["name"],
                    "titleid":   entry["titleid"],
                    "publisher": entry["publisher"],
                    "type":      entry["type"],
                })
                if len(results) >= limit:
                    break
    return results
