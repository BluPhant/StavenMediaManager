import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Job, MovieMatch
from ..services import job_manager

router = APIRouter(prefix="/jobs", tags=["jobs"])


class ExtractRequest(BaseModel):
    category: str
    item_name: str


class MoveRequest(BaseModel):
    category: str
    item_name: str


@router.get("")
def list_jobs(
    db: Session = Depends(get_db),
    active_only: bool = False,
    job_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
):
    q = db.query(Job).order_by(Job.created_at.desc())
    if active_only:
        q = q.filter(Job.status.in_(["pending", "running"]))
    if job_type:
        q = q.filter(Job.type == job_type)
    if status:
        q = q.filter(Job.status == status)
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


@router.post("/move", status_code=201)
def create_move_job(req: MoveRequest, db: Session = Depends(get_db)):
    item_path = os.path.join(settings.incoming_dir, req.category, req.item_name)
    if not os.path.isdir(item_path):
        raise HTTPException(status_code=404, detail="Item directory not found")

    match = (
        db.query(MovieMatch)
        .filter(MovieMatch.category == req.category, MovieMatch.item_name == req.item_name)
        .first()
    )
    if not match:
        raise HTTPException(
            status_code=400,
            detail="No IMDB match saved. Match the title before moving.",
        )

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
        type="move",
        category=req.category,
        item_name=req.item_name,
        source_path=item_path,
        status="pending",
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_manager.submit_move(job.id, item_path, match.formatted_name, req.category)
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail="Job is not active")
    job_manager.request_cancel(job_id)
    return {"ok": True, "message": "Cancel requested — job will stop at next checkpoint"}


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
