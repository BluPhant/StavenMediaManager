import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Job
from ..services import job_manager

router = APIRouter(prefix="/jobs", tags=["jobs"])


class ExtractRequest(BaseModel):
    category: str
    item_name: str


@router.get("")
def list_jobs(
    db: Session = Depends(get_db),
    active_only: bool = False,
    limit: int = 50,
):
    q = db.query(Job).order_by(Job.created_at.desc())
    if active_only:
        q = q.filter(Job.status.in_(["pending", "running"]))
    return q.limit(limit).all()


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/extract", status_code=201)
def create_extract_job(req: ExtractRequest, db: Session = Depends(get_db)):
    item_path = os.path.join(settings.incoming_dir, req.category, req.item_name)
    if not os.path.isdir(item_path):
        raise HTTPException(status_code=404, detail="Item directory not found")

    # Prevent duplicate active jobs on the same path
    existing = (
        db.query(Job)
        .filter(Job.source_path == item_path, Job.status.in_(["pending", "running"]))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A job is already active for this item (id={existing.id})",
        )

    job = Job(
        type="extract",
        category=req.category,
        item_name=req.item_name,
        source_path=item_path,
        status="pending",
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_manager.submit_extraction(job.id, item_path)
    return job


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("pending", "running"):
        raise HTTPException(status_code=400, detail="Cannot delete an active job")
    db.delete(job)
    db.commit()
    return {"ok": True}
