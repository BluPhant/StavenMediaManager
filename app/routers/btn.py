"""
BTN (BroadcasTheNet) API — search and grab torrents.

GET  /api/btn/status           — is BTN configured?
GET  /api/btn/search?q=&limit= — search via JSON-RPC API
POST /api/btn/grab             — resolve download URL then load into rTorrent
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..services.btn import BTNClient
from ..services.sources.rtorrent import RtorrentSource

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

    q     — search term
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
            "torrent_id": r.torrent_id,
            "title":      r.title,
            "series":     r.series,
            "size_bytes": r.size_bytes,
            "seeders":    r.seeders,
            "leechers":   r.leechers,
            "category":   r.category,
            "source":     r.source,
            "resolution": r.resolution,
            "codec":      r.codec,
            "info_url":   r.info_url,
            "pubdate":    r.pubdate,
        }
        for r in results
    ]


# ── Grab ──────────────────────────────────────────────────────────────────────

class GrabRequest(BaseModel):
    torrent_id: str          # BTN torrent ID — URL is resolved via getTorrentsUrl
    label: str = ""


@router.post("/grab", status_code=201)
def btn_grab(req: GrabRequest):
    """
    Resolve the .torrent download URL from BTN then load it into rTorrent.
    """
    if not _btn.is_configured():
        raise HTTPException(status_code=400, detail="BTN not configured. Set BTN_API_KEY.")

    rt = RtorrentSource()
    if not rt.is_configured():
        raise HTTPException(status_code=400, detail="rTorrent not configured.")

    label = req.label.strip() or settings.rtorrent_tag

    # 1. Resolve download URL from torrent ID
    logger.info(f"BTN grab: resolving URL for torrent_id={req.torrent_id}")
    try:
        torrent_url = _btn.get_torrent_url(req.torrent_id)
    except Exception as exc:
        logger.error(f"BTN getTorrentsUrl error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to resolve download URL: {exc}")

    if not torrent_url:
        raise HTTPException(status_code=502, detail="BTN returned an empty download URL.")

    # 2. Download .torrent bytes
    logger.info(f"BTN grab: fetching torrent from {torrent_url[:80]}…")
    try:
        torrent_bytes = _btn.fetch_torrent_bytes(torrent_url)
    except Exception as exc:
        logger.error(f"BTN grab fetch error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch .torrent: {exc}")

    # 3. Load into rTorrent
    logger.info(f"BTN grab: loading {len(torrent_bytes)} bytes into rTorrent (label={label!r})")
    try:
        rt.load_torrent(torrent_bytes, label=label)
    except Exception as exc:
        logger.error(f"BTN grab rTorrent error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to load into rTorrent: {exc}")

    return {"status": "ok", "label": label, "size": len(torrent_bytes)}
