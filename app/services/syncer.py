"""
Syncer — orchestrates polling sources and downloading ready items.

Each sync run creates a Job(type="sync") and processes all ready items
from all configured sources, updating progress as it goes.
"""
import logging
import os

from ..config import settings
from ..database import SessionLocal
from ..models import Job
from .job_manager import update_job
from .sources.rtorrent import RtorrentSource

logger = logging.getLogger(__name__)

# Registry of all sources — add new sources here as they're implemented
def _get_sources():
    sources = []
    rt = RtorrentSource()
    if rt.is_configured():
        sources.append(rt)
    return sources


def run_sync(job_id: int) -> None:
    """Main sync worker — runs in a background thread via job_manager."""
    update_job(job_id, status="running", progress=2, message="Connecting to sources...")

    sources = _get_sources()
    if not sources:
        update_job(job_id, status="error", message="No sources configured.")
        return

    # Gather all ready items across all sources
    all_items = []
    for source in sources:
        try:
            items = source.list_ready()
            logger.info(f"{source.__class__.__name__}: {len(items)} item(s) ready")
            all_items.extend((source, item) for item in items)
        except Exception as exc:
            update_job(job_id, status="error", message=f"Failed to list items: {exc}")
            return

    if not all_items:
        update_job(job_id, status="done", progress=100, message="Nothing new to import.")
        return

    total = len(all_items)
    update_job(job_id, progress=5, message=f"Found {total} item(s) to import.")

    downloaded = 0
    errors = []

    for idx, (source, item) in enumerate(all_items):
        dest_dir = os.path.join(settings.incoming_dir, item.suggested_type, item.name)
        base_pct = 5 + int(idx / total * 90)
        end_pct  = 5 + int((idx + 1) / total * 90)

        update_job(
            job_id,
            progress=base_pct,
            message=f"[{idx+1}/{total}] Downloading {item.name} → {item.suggested_type}/",
        )

        def _progress(pct: int, filename: str) -> None:
            scaled = base_pct + int(pct / 100 * (end_pct - base_pct))
            update_job(job_id, progress=scaled, message=f"{item.name} / {filename} {pct}%")

        try:
            source.download(item, dest_dir, progress_cb=_progress)
            source.mark_done(item)
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
        update_job(
            job_id, status="done", progress=100,
            message=f"Sync complete. {downloaded} item(s) imported.",
        )
