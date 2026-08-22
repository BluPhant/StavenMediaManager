from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Job
from ..services import job_manager
from ..services.sources import get_active_source
from ..services.sources.qbittorrent import QbittorrentSource
from ..services.sources.rtorrent import RtorrentSource

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/status")
def sources_status():
    """Return configuration status for all sources."""
    rt  = RtorrentSource()
    qbt = QbittorrentSource()
    return {
        "rtorrent": {
            "configured": rt.is_configured(),
            "url":        settings.rtorrent_url or None,
            "tag":        settings.rtorrent_tag,
            "ssh_host":   settings.rtorrent_ssh_host or None,
        },
        "qbittorrent": {
            "configured": qbt.is_configured(),
            "url":        settings.qbittorrent_url or None,
            "category":   settings.qbittorrent_category,
            "ssh_host":   settings.qbittorrent_ssh_host or None,
        },
        "active": "qbittorrent" if qbt.is_configured() else ("rtorrent" if rt.is_configured() else None),
    }


@router.post("/sync", status_code=201)
def trigger_sync(db: Session = Depends(get_db)):
    """Trigger an on-demand sync: poll all configured sources and download ready items."""
    source = get_active_source()
    if not source:
        raise HTTPException(
            status_code=400,
            detail="No sources configured. Set QBITTORRENT_URL or RTORRENT_URL with SSH credentials.",
        )

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
    """Return torrents currently in-progress on the seedbox."""
    source = get_active_source()
    if not source:
        raise HTTPException(status_code=400, detail="No seedbox source configured.")
    try:
        return source.list_active()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/torrent/{hash_}/stop", status_code=200)
def stop_torrent(hash_: str):
    """Stop (pause) a torrent on the seedbox by info-hash."""
    source = get_active_source()
    if not source:
        raise HTTPException(status_code=400, detail="No seedbox source configured.")
    try:
        source.stop_torrent(hash_)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"status": "stopped", "hash": hash_}


@router.post("/import/{hash_}", status_code=201)
def import_by_hash(hash_: str, db: Session = Depends(get_db)):
    """Import a specific torrent by hash, bypassing label and lookback filters."""
    source = get_active_source()
    if not source:
        raise HTTPException(status_code=400, detail="No seedbox source configured.")

    job = Job(
        type="sync",
        category="",
        item_name=f"Import {hash_[:8]}…",
        source_path="",
        status="pending",
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_manager.submit_import(job.id, hash_.upper())
    return job


@router.get("/brief")
def sources_brief():
    """Return all seedbox torrents as {hash: {name, label, pct}} — for duplicate detection."""
    source = get_active_source()
    if not source:
        raise HTTPException(status_code=400, detail="No seedbox source configured.")
    try:
        return source.list_all_brief()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/preview")
def preview_sync():
    """List torrents that would be imported on next sync (dry run — no download)."""
    source = get_active_source()
    if not source:
        raise HTTPException(status_code=400, detail="No seedbox source configured.")
    try:
        items = source.list_ready()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return [
        {
            "id":             it.id,
            "name":           it.name,
            "suggested_type": it.suggested_type,
            "size_bytes":     it.size_bytes,
            "label":          it.metadata.get("label", it.metadata.get("category", "")),
        }
        for it in items
    ]
