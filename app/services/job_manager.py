import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..database import SessionLocal
from ..models import Job, MovieSearch

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")
_cancel_flags: dict[int, threading.Event] = {}  # job_id → cancel event

# ── Scheduler config ──────────────────────────────────────────────────────────
QUEUE_CHECK_INTERVAL = 4 * 3600   # 4 hours — IPT availability check
SYNC_FAST_INTERVAL   = 300        # 5 minutes — after a grab
SYNC_FAST_WINDOW     = 7200       # 2 hours — fast polling duration
SYNC_SLOW_INTERVAL   = 3600      # 1 hour — normal polling

_last_grab_at: float = 0.0  # monotonic timestamp of most recent grab


def notify_grab() -> None:
    """Called after a torrent is sent to the seedbox. Switches sync to fast polling."""
    global _last_grab_at
    _last_grab_at = time.monotonic()
    logger.info("Grab notified — sync scheduler entering fast-poll mode.")


def request_cancel(job_id: int) -> None:
    """Signal a running job to stop at its next checkpoint."""
    if job_id in _cancel_flags:
        _cancel_flags[job_id].set()


def is_cancelled(job_id: int) -> bool:
    """Check whether a cancel has been requested for this job."""
    return _cancel_flags.get(job_id, threading.Event()).is_set()


def _register(job_id: int) -> None:
    _cancel_flags[job_id] = threading.Event()


def _unregister(job_id: int) -> None:
    _cancel_flags.pop(job_id, None)


def update_job(job_id: int, **kwargs) -> None:
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            for key, value in kwargs.items():
                setattr(job, key, value)
            job.updated_at = datetime.utcnow()
            db.commit()
    except Exception as exc:
        logger.error(f"Failed to update job {job_id}: {exc}")
    finally:
        db.close()


def submit_extraction(job_id: int, source_path: str) -> None:
    from ..services.extractor import run_extraction

    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_extraction, source_path)


def submit_extraction_chained(job_id: int, source_path: str, on_complete) -> None:
    """Extract archives, then call on_complete() if extraction succeeds."""
    from ..services.extractor import run_extraction

    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_extraction, source_path, on_complete)


def submit_switch_move(job_id: int, source_path: str, title_id: int,
                       content_id: int, dest_dir: str) -> None:
    from ..services.mover import run_switch_move

    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_switch_move, source_path, title_id, content_id, dest_dir)


def submit_move(job_id: int, source_path: str, formatted_name: str,
                category: str, imdb_id: str = "") -> None:
    from ..services.mover import run_move

    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_move, source_path, formatted_name, category, imdb_id)


def submit_music_import(job_id: int, source_path: str) -> None:
    from ..services.music import run_music_import

    _register(job_id)
    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_music_import, source_path)


def submit_sync(job_id: int) -> None:
    from ..services.syncer import run_sync

    _register(job_id)
    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_sync)


def submit_import(job_id: int, hash_: str) -> None:
    from ..services.syncer import run_import_by_hash

    _register(job_id)
    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_import_by_hash, hash_)


def submit_queue_check(job_id: int) -> None:
    from ..services.queue_checker import run_queue_check

    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_queue_check)


def submit_single_movie_check(job_id: int, imdb_id: str) -> None:
    from ..services.queue_checker import run_single_movie_check

    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_single_movie_check, imdb_id)


def start_queue_scheduler() -> None:
    """
    Start two daemon threads:
    1. Queue checker — fires every 4 hours to check IPT for queued movies.
    2. Sync poller — lightweight seedbox check. Fast (5 min) after a grab for
       2 hours, then slow (1 hour).  Only triggers a full sync when new
       completed torrents are detected.
    Called once from app startup.
    """
    def _queue_scheduler():
        while True:
            time.sleep(QUEUE_CHECK_INTERVAL)
            try:
                db = SessionLocal()
                try:
                    count = db.query(MovieSearch).filter(MovieSearch.queued == 1).count()
                finally:
                    db.close()

                if count == 0:
                    continue

                db2 = SessionLocal()
                try:
                    job = Job(
                        type="queue_check",
                        category="movies",
                        item_name=f"Queue check ({count} movies)",
                        source_path="",
                        status="pending",
                        progress=0,
                    )
                    db2.add(job)
                    db2.commit()
                    db2.refresh(job)
                    submit_queue_check(job.id)
                    logger.info(f"Queue scheduler: submitted job {job.id} for {count} queued movies.")
                finally:
                    db2.close()
            except Exception as exc:
                logger.warning(f"Queue scheduler error: {exc}")

    def _sync_poller():
        from ..config import settings
        from ..models import SyncedItem
        from .sources.rtorrent import RtorrentSource

        while True:
            # Adaptive interval
            since_grab = time.monotonic() - _last_grab_at
            interval = SYNC_FAST_INTERVAL if since_grab < SYNC_FAST_WINDOW else SYNC_SLOW_INTERVAL
            time.sleep(interval)

            try:
                rt = RtorrentSource()
                if not rt.is_configured() or not settings.rtorrent_tag:
                    continue

                # Lightweight check: completed torrents with our label not yet synced
                db = SessionLocal()
                try:
                    synced = {r.item_id for r in db.query(SyncedItem.item_id).filter(
                        SyncedItem.source == "rtorrent"
                    ).all()}
                finally:
                    db.close()

                new = rt.check_new_completions(settings.rtorrent_tag, synced)
                if not new:
                    continue

                names = [n for _, n in new]
                logger.info(f"Sync poller: {len(new)} new completion(s) detected: {names}")

                # Check no sync is already running
                db2 = SessionLocal()
                try:
                    running = db2.query(Job).filter(
                        Job.type == "sync",
                        Job.status.in_(["pending", "running"]),
                    ).first()
                    if running:
                        logger.info("Sync poller: sync already running, skipping.")
                        continue

                    job = Job(
                        type="sync",
                        category="",
                        item_name="Auto-sync (new completions)",
                        source_path="",
                        status="pending",
                        progress=0,
                    )
                    db2.add(job)
                    db2.commit()
                    db2.refresh(job)
                    submit_sync(job.id)
                    logger.info(f"Sync poller: auto-sync submitted (job {job.id})")
                finally:
                    db2.close()

            except Exception as exc:
                logger.warning(f"Sync poller error: {exc}")

    t1 = threading.Thread(target=_queue_scheduler, daemon=True, name="queue-scheduler")
    t2 = threading.Thread(target=_sync_poller, daemon=True, name="sync-poller")
    t1.start()
    t2.start()
    logger.info(f"Queue scheduler started (interval={QUEUE_CHECK_INTERVAL}s).")
    logger.info(f"Sync poller started (fast={SYNC_FAST_INTERVAL}s, slow={SYNC_SLOW_INTERVAL}s).")


def _guarded(job_id: int, func, *args) -> None:
    try:
        func(job_id, *args)
    except Exception as exc:
        logger.error(f"Job {job_id} raised unhandled exception: {exc}", exc_info=True)
        update_job(job_id, status="error", message=str(exc))
    finally:
        _unregister(job_id)
