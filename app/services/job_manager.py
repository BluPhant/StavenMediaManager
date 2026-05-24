import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..database import SessionLocal
from ..models import Job

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")


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


def _guarded(job_id: int, func, *args) -> None:
    try:
        func(job_id, *args)
    except Exception as exc:
        logger.error(f"Job {job_id} raised unhandled exception: {exc}", exc_info=True)
        update_job(job_id, status="error", message=str(exc))
