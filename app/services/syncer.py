"""
Syncer — orchestrates polling sources and downloading ready items.

Each sync run creates a Job(type="sync") and processes all ready items
from all configured sources, updating progress as it goes.
Skips any item whose hash is already recorded in synced_items.
"""
import logging
import os
import time
from datetime import datetime

from ..config import settings
from ..database import SessionLocal
from ..models import Job, MovieMatch, SyncedItem, SwitchContent, SwitchTitle
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


def _get_synced_ids(source: str) -> set[str]:
    """Return all item IDs already recorded for this source in a single DB query."""
    db = SessionLocal()
    try:
        rows = db.query(SyncedItem.item_id).filter(SyncedItem.source == source).all()
        return {r.item_id for r in rows}
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


def _auto_extract_if_needed(item_name: str, category: str, source_path: str) -> bool:
    """
    If source_path contains RAR archives, submit an extraction job and chain
    _auto_move_if_matched to run on completion.  Returns True if extraction
    was queued, False if no archives found (caller proceeds to _auto_move_if_matched).
    """
    from .extractor import has_rar_archives

    if not has_rar_archives(source_path):
        return False

    db = SessionLocal()
    try:
        job = Job(
            type="extract",
            category=category,
            item_name=item_name,
            source_path=source_path,
            status="pending",
            progress=0,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    finally:
        db.close()

    def _after_extract():
        _auto_move_if_matched(item_name, category, source_path)

    job_manager.submit_extraction_chained(job_id, source_path, _after_extract)
    logger.info(f"Auto-extract queued for '{item_name}' (job {job_id})")
    return True


def _auto_move_if_matched(item_name: str, category: str, source_path: str) -> bool:
    """
    Auto-process a freshly downloaded item when no user interaction is required:
      - movies — if a MovieMatch (TMDB IMDB lookup) already exists, submit a move job
      - music  — always submit a music_import job (FLAC→MP3 V0, tag/rename/organize,
                 relocate to /media/music/<Letter>/<Artist>/<Artist - Album (Year)>)
    Returns True if a job was submitted, False otherwise.
    Called from the sync worker after a successful download.
    """
    db = SessionLocal()
    try:
        if category in ("movies", "_unsorted"):
            match = (
                db.query(MovieMatch)
                .filter(MovieMatch.item_name == item_name)
                .first()
            )
            if not match:
                return False
            category = match.category  # use stored category (e.g. "movies") not "_unsorted"
            # Skip if a move job already ran for this source (prevents duplicates)
            recent = db.query(Job).filter(
                Job.source_path == source_path,
                Job.type == "move",
                Job.status.in_(["pending", "running", "done"]),
            ).first()
            if recent:
                logger.info(f"Skipping auto-move for '{item_name}' — job #{recent.id} already exists ({recent.status})")
                return False
            job = Job(
                type="move",
                category=category,
                item_name=item_name,
                source_path=source_path,
                status="pending",
                progress=0,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_manager.submit_move(job.id, source_path, match.formatted_name, category,
                                    imdb_id=getattr(match, "imdb_id", "") or "")
            logger.info(
                f"Auto-move queued: '{item_name}' → '{match.formatted_name}' (job {job.id})"
            )
            return True

        if category == "audiobooks":
            from ..models import AudiobookMatch
            match = (
                db.query(AudiobookMatch)
                .filter(AudiobookMatch.category == category,
                        AudiobookMatch.item_name == item_name)
                .first()
            )
            if not match:
                return False
            recent = db.query(Job).filter(
                Job.source_path == source_path,
                Job.type == "move",
                Job.status.in_(["pending", "running", "done"]),
            ).first()
            if recent:
                logger.info(f"Skipping auto-move for '{item_name}' — job #{recent.id} already exists")
                return False
            job = Job(
                type="move",
                category=category,
                item_name=item_name,
                source_path=source_path,
                status="pending",
                progress=0,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_manager.submit_move(job.id, source_path, match.formatted_name, category, imdb_id="")
            logger.info(f"Auto-move queued: '{item_name}' → '{match.formatted_name}' (job {job.id})")
            return True

        if category in ("switch-games", "switch games"):
            content = (
                db.query(SwitchContent)
                .filter(SwitchContent.item_name == item_name)
                .first()
            )
            if not content:
                return False
            title_rec = db.query(SwitchTitle).filter(SwitchTitle.id == content.title_id).first()
            if not title_rec:
                return False
            dest_dir = os.path.join(settings.media_dir, "games", "switch", title_rec.title)
            job = Job(
                type="move",
                category=category,
                item_name=item_name,
                source_path=source_path,
                status="pending",
                progress=0,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_manager.submit_switch_move(job.id, source_path,
                                           title_rec.id, content.id, dest_dir)
            logger.info(f"Auto switch-move queued: '{item_name}' → '{title_rec.title}' (job {job.id})")
            return True

        if category == "music":
            job = Job(
                type="music_import",
                category=category,
                item_name=item_name,
                source_path=source_path,
                status="pending",
                progress=0,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_manager.submit_music_import(job.id, source_path)
            logger.info(f"Auto music-import queued: '{item_name}' (job {job.id})")
            return True

        return False
    except Exception as exc:
        logger.warning(f"Auto-process setup failed for '{item_name}': {exc}")
        return False
    finally:
        db.close()


def run_import_by_hash(job_id: int, hash_: str) -> None:
    """Import a single torrent by hash, bypassing label and lookback filters."""
    update_job(job_id, status="running", progress=2, message="Fetching torrent info from seedbox…")

    sources = _get_sources()
    if not sources:
        update_job(job_id, status="error", message="No sources configured.")
        return

    source_name, source = sources[0]

    try:
        item = source.get_item_by_hash(hash_)
    except Exception as exc:
        update_job(job_id, status="error", message=f"Could not get torrent info: {exc}")
        return

    synced_ids = _get_synced_ids(source_name)
    if hash_ in synced_ids:
        update_job(job_id, status="done", progress=100, message=f"Already imported: {item.name}")
        return

    dest_dir = os.path.join(settings.incoming_dir, item.suggested_type, item.name)
    update_job(job_id, progress=5, message=f"{item.name} → {item.suggested_type}/")

    def _progress(pct: int, filename: str, mbps: float = 0.0) -> None:
        speed_str = f" @ {mbps:.1f} MB/s" if mbps > 0 else ""
        update_job(job_id, progress=5 + int(pct * 0.9),
                   message=f"{item.name} / {filename} {pct}%{speed_str}")

    try:
        source.download(item, dest_dir, progress_cb=_progress,
                        cancel_check=lambda: job_manager.is_cancelled(job_id))
        source.mark_done(item)
        _record_synced(source_name, item.id, item.name)
        logger.info(f"Imported by hash: {item.name} → {dest_dir}")
        queued = (
            _auto_extract_if_needed(item.name, item.suggested_type, dest_dir)
            or _auto_move_if_matched(item.name, item.suggested_type, dest_dir)
        )
        msg = f"Imported: {item.name}" + (" — processing queued." if queued else "")
        update_job(job_id, status="done", progress=100, message=msg)
    except Exception as exc:
        logger.error(f"Import by hash failed for {hash_}: {exc}", exc_info=True)
        update_job(job_id, status="error", progress=100, message=f"Import failed: {exc}")


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
            # Single DB query for all synced IDs — passed to list_ready() so it
            # can skip per-item XMLRPC calls for already-known torrents.
            t0 = time.monotonic()
            synced_ids = _get_synced_ids(source_name)
            logger.info(
                f"{source.__class__.__name__}: {len(synced_ids)} synced IDs "
                f"loaded from DB in {time.monotonic() - t0:.3f}s"
            )

            update_job(job_id, progress=3, message=f"Polling {source_name} ({len(synced_ids)} already synced)…")
            items = source.list_ready(exclude_ids=synced_ids)
            logger.info(f"{source.__class__.__name__}: {len(items)} new item(s) to import")
            all_items.extend((source_name, source, it) for it in items)
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
            source.download(item, dest_dir, progress_cb=_progress,
                            cancel_check=lambda: job_manager.is_cancelled(job_id))
            source.mark_done(item)
            _record_synced(source_name, item.id, item.name)
            downloaded += 1
            logger.info(f"Imported: {item.name} → {dest_dir}")
            if not _auto_extract_if_needed(item.name, item.suggested_type, dest_dir):
                _auto_move_if_matched(item.name, item.suggested_type, dest_dir)
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
