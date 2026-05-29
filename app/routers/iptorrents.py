"""
IPTorrents API — search and grab torrents.

GET  /api/iptorrents/status          — is IPT configured?
GET  /api/iptorrents/search?q=&cat=  — search via RSS feed
POST /api/iptorrents/grab            — fetch .torrent and load into rTorrent
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..services.iptorrents import IPTorrentsClient
from ..services.sources.rtorrent import RtorrentSource

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


# ── Grab ──────────────────────────────────────────────────────────────────────

class GrabRequest(BaseModel):
    torrent_url: str
    label: str = ""   # rTorrent label; defaults to RTORRENT_TAG if blank


@router.post("/grab", status_code=201)
def iptorrents_grab(req: GrabRequest):
    """
    Fetch the .torrent file from IPTorrents and load it into rTorrent.
    The label (ruTorrent tag) defaults to RTORRENT_TAG so the Sync job
    will pick it up automatically once it finishes downloading on the seedbox.
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

    # 2. Load into rTorrent
    logger.info(f"IPT grab: loading {len(torrent_bytes)} bytes into rTorrent (label={label!r})")
    try:
        rt.load_torrent(torrent_bytes, label=label)
    except Exception as exc:
        logger.error(f"IPT grab rTorrent error: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to load into rTorrent: {exc}")

    return {"status": "ok", "label": label, "size": len(torrent_bytes)}
