"""
IPTorrents API — search and grab torrents.

GET  /api/iptorrents/status          — is IPT configured?
GET  /api/iptorrents/search?q=&cat=  — search via RSS feed
POST /api/iptorrents/grab            — fetch .torrent and load into rTorrent
"""
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..services.iptorrents import IPTorrentsClient, build_search_cascade
from ..services.movies import auto_match_movie
from ..services.sources.rtorrent import RtorrentSource, extract_info_hash

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


# ── Grab ──────────────────────────────────────────────────────────────────────

class GrabRequest(BaseModel):
    torrent_url: str
    label: str = ""           # rTorrent label; defaults to RTORRENT_TAG if blank
    force: bool = False       # skip duplicate check
    title: str = ""           # search result title — used for auto TMDB match
    suggested_type: str = ""  # "movies" triggers auto-match


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

    # Auto-match movies to TMDB in the background so the match is ready by the
    # time the sync job downloads the file (enabling auto-move on sync).
    if req.suggested_type == "movies" and req.title and settings.tmdb_api_key:
        torrent_title = req.title.strip()
        api_key = settings.tmdb_api_key
        def _do_match():
            match = auto_match_movie(torrent_title, api_key, None)
            if match:
                logger.info(f"Auto-matched '{torrent_title}' → '{match.formatted_name}'")
            else:
                logger.info(f"Auto-match: no confident result for '{torrent_title}'")
        threading.Thread(target=_do_match, daemon=True).start()

    return {"status": "ok", "label": label, "size": len(torrent_bytes)}
