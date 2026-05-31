import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..database import SessionLocal
from ..models import Job

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")
_cancel_flags: dict[int, threading.Event] = {}  # job_id → cancel event


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


def submit_move(job_id: int, source_path: str, formatted_name: str, category: str) -> None:
    from ..services.mover import run_move

    update_job(job_id, status="running", progress=0)
    _executor.submit(_guarded, job_id, run_move, source_path, formatted_name, category)


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


def _guarded(job_id: int, func, *args) -> None:
    try:
        func(job_id, *args)
    except Exception as exc:
        logger.error(f"Job {job_id} raised unhandled exception: {exc}", exc_info=True)
        update_job(job_id, status="error", message=str(exc))
    finally:
        _unregister(job_id)
