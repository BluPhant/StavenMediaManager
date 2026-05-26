"""
Syncer — orchestrates polling sources and downloading ready items.

Each sync run creates a Job(type="sync") and processes all ready items
from all configured sources, updating progress as it goes.
Skips any item whose hash is already recorded in synced_items.
"""
import logging
import os
from datetime import datetime

from ..config import settings
from ..database import SessionLocal
from ..models import SyncedItem
from . import job_manager
from .job_manager import update_job
from .sources.rtorrent import RtorrentSource

logger = logging.getLogger(__name__)


def _get_sources():
    sources = []
    rt = RtorrentSource()
    if rt.is_configured():
        sources.append(("rtorrent", rt))
    return sources


def _already_synced(source: str, item_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.query(SyncedItem).filter(
            SyncedItem.source == source,
            SyncedItem.item_id == item_id,
        ).first() is not None
    finally:
        db.close()


def _record_synced(source: str, item_id: str, name: str) -> None:
    db = SessionLocal()
    try:
        record = SyncedItem(source=source, item_id=item_id, name=name,
                            synced_at=datetime.utcnow())
        db.merge(record)
        db.commit()
    except Exception as exc:
        logger.warning(f"Could not record synced item {item_id}: {exc}")
    finally:
        db.close()


def run_sync(job_id: int) -> None:
    """Main sync worker — runs in a background thread via job_manager."""
    update_job(job_id, status="running", progress=2, message="Connecting to sources...")

    sources = _get_sources()
    if not sources:
        update_job(job_id, status="error", message="No sources configured.")
        return

    # Gather ready items, skipping already-imported hashes
    all_items = []
    for source_name, source in sources:
        try:
            items = source.list_ready()
            new_items = [it for it in items if not _already_synced(source_name, it.id)]
            skipped = len(items) - len(new_items)
            logger.info(
                f"{source.__class__.__name__}: {len(items)} tagged, "
                f"{skipped} already imported, {len(new_items)} new"
            )
            all_items.extend((source_name, source, it) for it in new_items)
        except Exception as exc:
            update_job(job_id, status="error", message=f"Failed to list items: {exc}")
            return

    if not all_items:
        update_job(job_id, status="done", progress=100,
                   message="Nothing new to import (all tagged items already synced).")
        return

    total = len(all_items)
    update_job(job_id, progress=5, message=f"Found {total} new item(s) to import.")

    downloaded = 0
    errors = []

    for idx, (source_name, source, item) in enumerate(all_items):
        # Check for cancellation before each download
        if job_manager.is_cancelled(job_id):
            update_job(job_id, status="cancelled",
                       message=f"Cancelled after {downloaded}/{total} item(s).")
            return

        dest_dir = os.path.join(settings.incoming_dir, item.suggested_type, item.name)
        base_pct = 5 + int(idx / total * 90)
        end_pct  = 5 + int((idx + 1) / total * 90)

        update_job(
            job_id,
            progress=base_pct,
            message=f"[{idx+1}/{total}] {item.name} → {item.suggested_type}/",
        )

        def _progress(pct: int, filename: str, mbps: float = 0.0, _base=base_pct, _end=end_pct, _name=item.name) -> None:
            scaled = _base + int(pct / 100 * (_end - _base))
            speed_str = f" @ {mbps:.1f} MB/s" if mbps > 0 else ""
            update_job(job_id, progress=scaled, message=f"{_name} / {filename} {pct}%{speed_str}")

        try:
            source.download(item, dest_dir, progress_cb=_progress)
            source.mark_done(item)
            _record_synced(source_name, item.id, item.name)
            downloaded += 1
            logger.info(f"Imported: {item.name} → {dest_dir}")
        except Exception as exc:
            logger.error(f"Failed to import {item.name}: {exc}", exc_info=True)
            errors.append(f"{item.name}: {exc}")

    if errors:
        msg = f"Done. {downloaded}/{total} imported. Errors: {'; '.join(errors)}"
        update_job(job_id, status="error" if downloaded == 0 else "done",
                   progress=100, message=msg)
    else:
        update_job(job_id, status="done", progress=100,
                   message=f"Sync complete. {downloaded} item(s) imported.")
