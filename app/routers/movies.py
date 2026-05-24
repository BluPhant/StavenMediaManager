from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import MovieMatch
from ..services.movies import clean_for_search, search_tmdb

router = APIRouter(prefix="/movies", tags=["movies"])


# ── Request/response models ───────────────────────────────────────────────────

class MatchRequest(BaseModel):
    category: str
    item_name: str
    tmdb_id: int
    title: str
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    formatted_name: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/search")
def search_movies(
    q: str = Query(..., min_length=1),
    year: int | None = None,
):
    if not settings.tmdb_api_key:
        raise HTTPException(
            status_code=503,
            detail="TMDB_API_KEY is not configured. Set the environment variable and restart.",
        )
    try:
        return search_tmdb(q, year, settings.tmdb_api_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/match")
def get_match(
    category: str = Query(...),
    item: str = Query(...),
    db: Session = Depends(get_db),
):
    """Return the saved match for an item plus a pre-cleaned search suggestion."""
    match = (
        db.query(MovieMatch)
        .filter(MovieMatch.category == category, MovieMatch.item_name == item)
        .first()
    )
    query, year = clean_for_search(item)
    return {
        "match": _serialize(match) if match else None,
        "suggested_query": query,
        "suggested_year": year,
    }


@router.get("/matches")
def list_matches(
    category: str = Query(...),
    db: Session = Depends(get_db),
):
    """Return all matches for a category (used to badge items in the list view)."""
    rows = db.query(MovieMatch).filter(MovieMatch.category == category).all()
    return {r.item_name: _serialize(r) for r in rows}


@router.post("/match", status_code=201)
def save_match(req: MatchRequest, db: Session = Depends(get_db)):
    existing = (
        db.query(MovieMatch)
        .filter(MovieMatch.category == req.category, MovieMatch.item_name == req.item_name)
        .first()
    )
    if existing:
        existing.tmdb_id = req.tmdb_id
        existing.formatted_name = req.formatted_name
        existing.year = req.year
        existing.poster_url = req.poster_url
        existing.overview = req.overview
    else:
        existing = MovieMatch(
            category=req.category,
            item_name=req.item_name,
            tmdb_id=req.tmdb_id,
            formatted_name=req.formatted_name,
            year=req.year,
            poster_url=req.poster_url,
            overview=req.overview,
        )
        db.add(existing)
    db.commit()
    db.refresh(existing)
    return _serialize(existing)


@router.delete("/match")
def clear_match(
    category: str = Query(...),
    item: str = Query(...),
    db: Session = Depends(get_db),
):
    match = (
        db.query(MovieMatch)
        .filter(MovieMatch.category == category, MovieMatch.item_name == item)
        .first()
    )
    if match:
        db.delete(match)
        db.commit()
    return {"ok": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(m: MovieMatch) -> dict:
    return {
        "id": m.id,
        "category": m.category,
        "item_name": m.item_name,
        "formatted_name": m.formatted_name,
        "tmdb_id": m.tmdb_id,
        "year": m.year,
        "poster_url": m.poster_url,
        "overview": m.overview,
    }
