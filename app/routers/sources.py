from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Job
from ..services import job_manager
from ..services.sources.rtorrent import RtorrentSource

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/status")
def sources_status():
    """Return configuration status for all sources."""
    rt = RtorrentSource()
    return {
        "rtorrent": {
            "configured": rt.is_configured(),
            "url": settings.rtorrent_url or None,
            "tag": settings.rtorrent_tag,
            "ssh_host": settings.rtorrent_ssh_host or None,
        }
    }


@router.post("/sync", status_code=201)
def trigger_sync(db: Session = Depends(get_db)):
    """Trigger an on-demand sync: poll all configured sources and download ready items."""
    rt = RtorrentSource()
    if not rt.is_configured():
        raise HTTPException(
            status_code=400,
            detail="No sources configured. Set RTORRENT_URL, RTORRENT_SSH_HOST, and RTORRENT_SSH_USER.",
        )

    # Prevent duplicate active sync jobs
    existing = (
        db.query(Job)
        .filter(Job.type == "sync", Job.status.in_(["pending", "running"]))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A sync job is already active (id={existing.id})",
        )

    job = Job(
        type="sync",
        category="",
        item_name="Seedbox sync",
        source_path="",
        status="pending",
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_manager.submit_sync(job.id)
    return job


@router.get("/active")
def active_torrents():
    """Return torrents currently in-progress on the seedbox (tagged, not yet complete)."""
    rt = RtorrentSource()
    if not rt.is_configured():
        raise HTTPException(status_code=400, detail="rTorrent source not configured.")
    try:
        return rt.list_active()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/preview")
def preview_sync():
    """List torrents that would be imported on next sync (dry run — no download)."""
    rt = RtorrentSource()
    if not rt.is_configured():
        raise HTTPException(status_code=400, detail="rTorrent source not configured.")
    try:
        items = rt.list_ready()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return [
        {
            "id": it.id,
            "name": it.name,
            "suggested_type": it.suggested_type,
            "size_bytes": it.size_bytes,
            "label": it.metadata.get("label", ""),
        }
        for it in items
    ]
