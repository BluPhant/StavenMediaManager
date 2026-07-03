"""
IPTorrents API — search, browse, and grab torrents.

GET  /api/iptorrents/status          — is IPT configured?
GET  /api/iptorrents/search?q=&cat=  — search via RSS feed
GET  /api/iptorrents/browse          — recently uploaded movies (TMDB-enriched)
GET  /api/iptorrents/browse-switch   — recently uploaded Switch games (library-matched)
POST /api/iptorrents/grab            — fetch .torrent and load into rTorrent
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..services.iptorrents import IPTorrentsClient, build_search_cascade, parse_query
from ..services.movies import auto_match_movie, search_tmdb
from ..services.sources.rtorrent import RtorrentSource, extract_info_hash, extract_torrent_name

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/iptorrents", tags=["iptorrents"])

_ipt = IPTorrentsClient()


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
def iptorrents_status():
    rt = RtorrentSource()
    return {
        "iptorrents": {
            "configured": _ipt.is_configured(),
            "domain": settings.iptorrents_domain or "iptorrents.com",
        },
        "rtorrent": {
            "configured": rt.is_configured(),
        },
    }


# ── Search ────────────────────────────────────────────────────────────────────

@router.get("/search")
def iptorrents_search(q: str = "", cat: str = "all", limit: int = 50):
    """
    Search IPTorrents RSS.

    q     — search term (empty = recent items for the category)
    cat   — all | movies | tv | music | audiobooks | games | ebooks | software
    limit — max results (1–100)
    """
    if not _ipt.is_configured():
        raise HTTPException(
            status_code=400,
            detail="IPTorrents not configured. Set IPTORRENTS_USER_ID and IPTORRENTS_PASSKEY.",
        )
    limit = max(1, min(limit, 100))
    try:
        results = _ipt.search(query=q, category=cat, limit=limit)
    except Exception as exc:
        logger.error(f"IPT search error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    return [
        {
            "torrent_id":     r.torrent_id,
            "title":          r.title,
            "size_bytes":     r.size_bytes,
            "seeders":        r.seeders,
            "leechers":       r.leechers,
            "ipt_category":   r.ipt_category,
            "suggested_type": r.suggested_type,
            "torrent_url":    r.torrent_url,
            "info_url":       r.info_url,
            "pubdate":        r.pubdate,
        }
        for r in results
    ]


# ── Smart search ─────────────────────────────────────────────────────────────

@router.get("/smart-search")
def iptorrents_smart_search(q: str = "", cat: str = "all", limit: int = 50):
    """
    Progressive search — tries increasingly broad queries until results appear.

    Response includes:
      results      — list of torrent results
      query_used   — the query that actually returned results
      year         — year detected in the original query, if any
      attempts     — all queries tried in order
    """
    if not _ipt.is_configured():
        raise HTTPException(
            status_code=400,
            detail="IPTorrents not configured. Set IPTORRENTS_USER_ID and IPTORRENTS_PASSKEY.",
        )
    limit = max(1, min(limit, 100))
    try:
        data = _ipt.smart_search(q=q, category=cat, limit=limit)
    except Exception as exc:
        logger.error(f"IPT smart_search error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "results": [
            {
                "torrent_id":     r.torrent_id,
                "title":          r.title,
                "size_bytes":     r.size_bytes,
                "seeders":        r.seeders,
                "leechers":       r.leechers,
                "ipt_category":   r.ipt_category,
                "suggested_type": r.suggested_type,
                "torrent_url":    r.torrent_url,
                "info_url":       r.info_url,
                "pubdate":        r.pubdate,
            }
            for r in data["results"]
        ],
        "query_used": data["query_used"],
        "year":       data["year"],
        "attempts":   data["attempts"],
    }


# ── Browse (recently uploaded movies, TMDB-enriched) ─────────────────────────

_browse_cache: list | None = None
_browse_cache_at: float = 0.0
_BROWSE_TTL = 300.0  # 5 minutes


@router.get("/browse")
def iptorrents_browse(limit: int = 20, offset: int = 0):
    """
    Recently uploaded movies from IPT, grouped by title and enriched with
    TMDB metadata (poster, rating, genres).  Cached for 5 minutes.
    """
    global _browse_cache, _browse_cache_at

    now = time.monotonic()
    if _browse_cache is not None and (now - _browse_cache_at) < _BROWSE_TTL:
        page = _browse_cache[offset:offset + limit]
        return {"items": page, "total": len(_browse_cache), "offset": offset}

    if not _ipt.is_configured():
        raise HTTPException(status_code=400, detail="IPTorrents not configured.")
    if not settings.tmdb_api_key:
        raise HTTPException(status_code=503, detail="TMDB API key not configured.")

    try:
        raw = _ipt.search(query="", category="movies", limit=100)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Group releases by movie (title + year extracted from torrent name)
    groups: dict[str, dict] = {}
    for r in raw:
        parsed = parse_query(r.title)
        title_clean = parsed["title"]
        year_str = parsed["year"]
        year = int(year_str) if year_str else None
        if not title_clean:
            continue
        key = f"{title_clean.lower()}|{year or ''}"
        if key not in groups:
            groups[key] = {
                "title": title_clean, "year": year,
                "releases": [], "best_res": None, "best_rank": -1,
                "total_seeds": 0,
            }
        g = groups[key]
        g["releases"].append(r)
        g["total_seeds"] += r.seeders
        # Track best resolution
        res = _browse_extract_res(r.title)
        rank = _BROWSE_RES_RANK.get(res, 0) if res else 0
        if rank > g["best_rank"]:
            g["best_rank"] = rank
            g["best_res"] = res

    # Sort by recency (first appearance in RSS = most recent)
    unique = list(groups.values())

    # Enrich with TMDB (parallel, max 8 threads)
    api_key = settings.tmdb_api_key
    enriched = []

    def _enrich(idx, g):
        try:
            results = search_tmdb(g["title"], g["year"], api_key)
            if results:
                return (idx, {**g, "tmdb": results[0]})
        except Exception:
            pass
        return (idx, {**g, "tmdb": None})

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_enrich, i, g) for i, g in enumerate(unique)]
        for f in as_completed(futures):
            enriched.append(f.result())

    enriched.sort(key=lambda pair: pair[0])
    enriched = [e for _, e in enriched]

    # Build response
    result = []
    for e in enriched:
        tmdb = e.get("tmdb") or {}
        result.append({
            "title":       tmdb.get("title") or e["title"],
            "year":        tmdb.get("year") or e["year"],
            "tmdb_id":     tmdb.get("tmdb_id"),
            "poster_url":  tmdb.get("poster_url"),
            "overview":    (tmdb.get("overview") or "")[:200],
            "formatted_name": tmdb.get("formatted_name") or e["title"],
            "best_res":    e["best_res"],
            "total_seeds": e["total_seeds"],
            "release_count": len(e["releases"]),
        })

    _browse_cache = result
    _browse_cache_at = now
    logger.info(f"IPT browse: {len(result)} unique movies from {len(raw)} releases")
    page = result[offset:offset + limit]
    return {"items": page, "total": len(result), "offset": offset}


import re as _re

_BROWSE_RES_RE = _re.compile(r"\b(2160p|1080p|720p|480p|4[Kk]|UHD)\b", _re.IGNORECASE)
_BROWSE_RES_RANK = {"2160p": 4, "4k": 4, "uhd": 4, "1080p": 2, "720p": 1, "480p": 0}


def _browse_extract_res(title: str) -> str | None:
    m = _BROWSE_RES_RE.search(title)
    return m.group(1).lower() if m else None


# ── Browse Switch (recently uploaded Switch games, library-matched) ───────────

import re as _sw_re

# Strip common Switch release tags and scene suffixes from a torrent title
_SW_STRIP = _sw_re.compile(
    r'\s*[\[\(]'
    r'(?:Switch|NSW|NSP|XCI|NSZ|NCA|eShop|Retail|Digital|DLC|Update|v[\d.]+|'
    r'[Ee]n[Gg]lish|MULTI\d*|[\d]+[\d.]*p?|NMoS|TENOKE|VENOM|BigBlueBox|'
    r'TiNYiSO|SiMPLEX|CODEX|PLAZA|RELOADED|FLT|GOG|HI2U|'
    r'SKIDROW|PROPHET|CPY|EMPRESS|FitGirl|KaOs|DODI|iGG|ElAmigos|'
    r'[A-Z0-9]{3,8})\s*[\]\)]',
    _sw_re.IGNORECASE,
)
_SW_DASH_GROUP = _sw_re.compile(r'\s*-\s*[A-Z0-9]{3,12}\s*$')
_SW_VERSION    = _sw_re.compile(r'\s+v[\d.]+\s*$', _sw_re.IGNORECASE)


def _parse_switch_title(raw: str) -> str:
    t = raw
    t = _SW_STRIP.sub('', t)
    t = _SW_VERSION.sub('', t)
    t = _SW_DASH_GROUP.sub('', t)
    return t.strip(' .-_')


_sw_browse_cache: list | None = None
_sw_browse_cache_at: float = 0.0
_SW_BROWSE_TTL = 300.0


@router.get("/browse-switch")
def browse_switch(limit: int = 8, db: Session = Depends(get_db)):
    """
    Recently uploaded Switch games from IPT category 47, matched against
    the local Switch library for cover art. Cached for 5 minutes.
    """
    global _sw_browse_cache, _sw_browse_cache_at

    now = time.monotonic()
    if _sw_browse_cache is not None and (now - _sw_browse_cache_at) < _SW_BROWSE_TTL:
        return {"items": _sw_browse_cache[:limit]}

    if not _ipt.is_configured():
        raise HTTPException(status_code=400, detail="IPTorrents not configured.")

    try:
        raw = _ipt.search(query="", category="switch", limit=50)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Deduplicate by cleaned title
    seen: dict[str, dict] = {}
    for r in raw:
        title = _parse_switch_title(r.title)
        if not title:
            continue
        key = title.lower()
        if key not in seen:
            seen[key] = {"title": title, "seeders": r.seeders, "torrent_url": r.torrent_url}
        else:
            seen[key]["seeders"] = max(seen[key]["seeders"], r.seeders)

    # Match against local switch_titles for cover art / metadata
    from ..models import SwitchTitle
    from sqlalchemy import func as _func

    all_db_titles = db.query(SwitchTitle).all()
    db_by_lower = {t.title.lower(): t for t in all_db_titles}

    def _best_match(title: str):
        key = title.lower()
        if key in db_by_lower:
            return db_by_lower[key]
        # Partial match: any DB title that starts with or contains the key
        for k, t in db_by_lower.items():
            if k.startswith(key[:min(len(key), 12)]) or key[:min(len(key), 12)] in k:
                return t
        return None

    result = []
    for entry in seen.values():
        match = _best_match(entry["title"])
        result.append({
            "title":       match.title if match else entry["title"],
            "cover_url":   match.cover_url if match else None,
            "cover_local": match.cover_local if match else None,
            "publisher":   match.publisher if match else None,
            "genres":      match.genres if match else None,
            "release_date": match.release_date if match else None,
            "in_library":  match is not None,
            "seeders":     entry["seeders"],
            "torrent_url": entry["torrent_url"],
        })

    _sw_browse_cache = result
    _sw_browse_cache_at = now
    logger.info(f"IPT browse-switch: {len(result)} unique titles from {len(raw)} releases")
    return {"items": result[:limit]}


# ── Grab ──────────────────────────────────────────────────────────────────────

class GrabRequest(BaseModel):
    torrent_url: str
    label: str = ""           # rTorrent label; defaults to RTORRENT_TAG if blank
    force: bool = False       # skip duplicate check
    title: str = ""           # search result title — used for auto TMDB match
    suggested_type: str = ""  # "movies" triggers auto-match
    imdb_id: str = ""         # if set, links the grab to a movie_searches record


@router.post("/grab", status_code=201)
def iptorrents_grab(req: GrabRequest, db: Session = Depends(get_db)):
    """
    Fetch the .torrent file from IPTorrents and load it into rTorrent.
    Returns 409 if the torrent is already on the seedbox (same info-hash).
    Pass force=true to load anyway.
    """
    if not _ipt.is_configured():
        raise HTTPException(
            status_code=400,
            detail="IPTorrents not configured. Set IPTORRENTS_USER_ID and IPTORRENTS_PASSKEY.",
        )

    rt = RtorrentSource()
    if not rt.is_configured():
        raise HTTPException(
            status_code=400,
            detail="rTorrent not configured. Set RTORRENT_URL and credentials.",
        )

    label = req.label.strip() or settings.rtorrent_tag

    # 1. Download .torrent bytes from IPT
    logger.info(f"IPT grab: fetching torrent from {req.torrent_url[:80]}…")
    try:
        torrent_bytes = _ipt.fetch_torrent_bytes(req.torrent_url)
    except Exception as exc:
        logger.error(f"IPT grab fetch error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch .torrent: {exc}")

    # 2. Hash check — is this torrent already on the seedbox?
    if not req.force:
        try:
            info_hash = extract_info_hash(torrent_bytes)
            brief = rt.list_all_brief()
            if info_hash in brief:
                existing = brief[info_hash]
                logger.info(f"IPT grab: duplicate detected hash={info_hash} name={existing['name']!r}")
                raise HTTPException(
                    status_code=409,
                    detail={
                        "conflict": True,
                        "hash":     info_hash,
                        "name":     existing["name"],
                        "label":    existing["label"],
                        "pct":      existing["pct"],
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(f"IPT grab hash-check failed (non-fatal): {exc}")

    # 3. Load into rTorrent
    logger.info(f"IPT grab: loading {len(torrent_bytes)} bytes into rTorrent (label={label!r})")
    try:
        rt.load_torrent(torrent_bytes, label=label)
    except Exception as exc:
        logger.error(f"IPT grab rTorrent error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to load into rTorrent: {exc}")

    # Resolve the info hash for linking to movie_searches
    info_hash = ""
    try:
        info_hash = extract_info_hash(torrent_bytes)
    except Exception:
        pass

    # If this grab is linked to a movie_searches record, store the hash
    if req.imdb_id and info_hash:
        def _link_movie():
            from ..database import SessionLocal
            from ..models import MovieSearch
            from datetime import datetime
            db = SessionLocal()
            try:
                movie = db.query(MovieSearch).filter(
                    MovieSearch.imdb_id == req.imdb_id
                ).first()
                if movie:
                    movie.sbx_hash       = info_hash
                    movie.sbx_pct        = 0
                    movie.sbx_checked_at = datetime.utcnow()
                    movie.status         = "grabbed"
                    db.commit()
                    logger.info(
                        f"Linked grab {info_hash} → movie_searches({req.imdb_id})"
                    )
            finally:
                db.close()
        threading.Thread(target=_link_movie, daemon=True).start()

    # Create MovieMatch so the auto-move works when the sync downloads the file.
    # Use the torrent's actual directory name (from .torrent metadata) — not the RSS
    # title, which may differ.  If imdb_id is known (from discover flow), create
    # directly from MovieSearch; otherwise fall back to auto_match_movie.
    if req.suggested_type == "movies" and settings.tmdb_api_key:
        def _do_match():
            try:
                torrent_name = extract_torrent_name(torrent_bytes)
            except Exception:
                torrent_name = req.title.strip()

            if req.imdb_id:
                _ensure_movie_match_from_imdb(torrent_name, req.imdb_id)
            elif req.title:
                match = auto_match_movie(torrent_name, settings.tmdb_api_key, None)
                if match:
                    logger.info(f"Auto-matched '{torrent_name}' → '{match.formatted_name}'")
                else:
                    logger.info(f"Auto-match: no confident result for '{torrent_name}'")
        threading.Thread(target=_do_match, daemon=True).start()

    # Notify the sync scheduler that a grab happened (triggers fast polling)
    from ..services.job_manager import notify_grab
    notify_grab()

    return {"status": "ok", "label": label, "size": len(torrent_bytes), "hash": info_hash}


def _ensure_movie_match_from_imdb(torrent_name: str, imdb_id: str) -> None:
    """Create a MovieMatch directly from the MovieSearch record (reliable — no title parsing)."""
    from ..database import SessionLocal
    from ..models import MovieMatch, MovieSearch
    from ..services.movies import get_tmdb_details

    db = SessionLocal()
    try:
        existing = db.query(MovieMatch).filter(
            MovieMatch.category == "movies",
            MovieMatch.item_name == torrent_name,
        ).first()
        if existing:
            logger.info(f"MovieMatch already exists for '{torrent_name}'")
            return

        ms = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
        if ms and ms.tmdb_id:
            details = get_tmdb_details(ms.tmdb_id, settings.tmdb_api_key)
            if details:
                m = MovieMatch(
                    category="movies",
                    item_name=torrent_name,
                    tmdb_id=ms.tmdb_id,
                    imdb_id=imdb_id,
                    formatted_name=details.get("formatted_name", ms.title),
                    year=details.get("year") or ms.year,
                    poster_url=details.get("poster_url") or ms.poster_url,
                    overview=details.get("overview") or ms.overview,
                )
                db.add(m)
                db.commit()
                logger.info(f"MovieMatch created: '{torrent_name}' → '{m.formatted_name}' (imdb={imdb_id})")
                return

        logger.warning(f"Could not create MovieMatch from imdb_id={imdb_id} for '{torrent_name}'")
    except Exception as exc:
        logger.warning(f"MovieMatch creation failed for '{torrent_name}': {exc}")
    finally:
        db.close()
