"""
BTN (BroadcasTheNet) API — search and grab torrents.

GET  /api/btn/status           — is BTN configured?
GET  /api/btn/search?q=&limit= — search via JSON-RPC API
POST /api/btn/grab             — fetch .torrent and load into rTorrent
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..services.btn import BTNClient
from ..services.sources import get_active_source

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/btn", tags=["btn"])

_btn = BTNClient()


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
def btn_status():
    return {"btn": {"configured": _btn.is_configured()}}


# ── Search ────────────────────────────────────────────────────────────────────

@router.get("/search")
def btn_search(q: str = "", limit: int = 50):
    """
    Search BTN via getTorrentsSearch JSON-RPC.

    q     — series name to search
    limit — max results (1–100)
    """
    if not _btn.is_configured():
        raise HTTPException(status_code=400, detail="BTN not configured. Set BTN_API_KEY.")
    limit = max(1, min(limit, 100))
    try:
        results = _btn.search(query=q, limit=limit)
    except Exception as exc:
        logger.error(f"BTN search error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    return [
        {
            "torrent_id":  r.torrent_id,
            "title":       r.title,
            "series":      r.series,
            "size_bytes":  r.size_bytes,
            "seeders":     r.seeders,
            "leechers":    r.leechers,
            "category":    r.category,
            "source":      r.source,
            "resolution":  r.resolution,
            "codec":       r.codec,
            "torrent_url": r.torrent_url,
            "info_url":    r.info_url,
            "pubdate":     r.pubdate,
        }
        for r in results
    ]


# ── Grab ──────────────────────────────────────────────────────────────────────

class GrabRequest(BaseModel):
    torrent_url: str      # direct .torrent download URL from search response
    label: str = ""
    title: str = ""           # search result title (reserved for future auto-match)
    suggested_type: str = ""  # "movies" would trigger auto-match


@router.post("/grab", status_code=201)
def btn_grab(req: GrabRequest):
    """
    Fetch the .torrent file from BTN and load it into rTorrent.
    """
    if not _btn.is_configured():
        raise HTTPException(status_code=400, detail="BTN not configured. Set BTN_API_KEY.")

    source = get_active_source()
    if not source:
        raise HTTPException(status_code=400, detail="No seedbox source configured.")

    label = req.label.strip() or source.default_category

    logger.info(f"BTN grab: fetching {req.torrent_url[:80]}…")
    try:
        torrent_bytes = _btn.fetch_torrent_bytes(req.torrent_url)
    except Exception as exc:
        logger.error(f"BTN grab fetch error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch .torrent: {exc}")

    logger.info(f"BTN grab: loading {len(torrent_bytes)} bytes into seedbox (label={label!r})")
    try:
        source.load_torrent(torrent_bytes, label=label)
    except Exception as exc:
        logger.error(f"BTN grab load error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to load torrent: {exc}")

    return {"status": "ok", "label": label, "size": len(torrent_bytes)}
