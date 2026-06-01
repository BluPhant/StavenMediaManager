"""
Movies router — IMDB-keyed movie discovery, tracking, queue, and upgrade reviews.

GET  /api/movies/search?q=         — TMDB title search (returns candidates)
POST /api/movies/confirm           — confirm a TMDB pick, check Plex/SBX/IPT
GET  /api/movies/history           — all searched movies (sorted by last_searched)
POST /api/movies/queue/{imdb_id}   — add to watching queue
DELETE /api/movies/queue/{imdb_id} — remove from queue
GET  /api/movies/queue             — all queued movies
GET  /api/movies/reviews           — pending upgrade reviews
POST /api/movies/reviews/{id}/confirm — delete trashed copy, close review
POST /api/movies/reviews/{id}/revert  — restore old copy, delete new

Legacy endpoints (still used by incoming-item TMDB matching workflow):
GET  /api/movies/match
GET  /api/movies/matches
POST /api/movies/match
DELETE /api/movies/match
"""
import logging
import os
import re
import shutil
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import MovieMatch, MovieSearch, UpgradeReview
from ..services import plex as plex_svc
from ..services.movies import clean_for_search, get_tmdb_details, search_tmdb

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/movies", tags=["movies"])

# Resolution tier ranking (shared with queue_checker / plex service)
_RES_RANK: dict[str, int] = {
    "2160p": 4, "4k": 4,
    "1440p": 3,
    "1080p": 2,
    "720p": 1,
    "480p": 0,
}
_RES_RE = re.compile(r"\b(2160p|1440p|1080p|720p|480p|4[Kk]|UHD)\b", re.IGNORECASE)


# ── Request / Response models ─────────────────────────────────────────────────

class ConfirmRequest(BaseModel):
    tmdb_id: int


class QueueRequest(BaseModel):
    min_resolution: str = "2160p"   # '2160p' | '1080p' | '720p'


class MatchRequest(BaseModel):
    category: str
    item_name: str
    tmdb_id: int
    title: str
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    formatted_name: str


# ── New discovery endpoints ───────────────────────────────────────────────────

@router.get("/search")
def search_movies(q: str = Query(..., min_length=1), year: int | None = None):
    """TMDB title search — returns candidate list for the confirm step."""
    if not settings.tmdb_api_key:
        raise HTTPException(status_code=503, detail="TMDB_API_KEY is not configured.")
    try:
        return search_tmdb(q, year, settings.tmdb_api_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/confirm")
def confirm_movie(req: ConfirmRequest, db: Session = Depends(get_db)):
    """
    Confirm a movie selection (by TMDB ID).
    Fetches full TMDB details (incl. IMDB ID), then checks Plex, seedbox, and IPT
    in parallel.  Upserts a movie_searches record.  Returns unified status.
    """
    if not settings.tmdb_api_key:
        raise HTTPException(status_code=503, detail="TMDB_API_KEY is not configured.")

    # 1. Get TMDB details + IMDB ID
    details = get_tmdb_details(req.tmdb_id, settings.tmdb_api_key)
    if not details:
        raise HTTPException(status_code=404, detail="Movie not found on TMDB.")
    imdb_id = details.get("imdb_id", "")
    if not imdb_id:
        raise HTTPException(status_code=422,
                            detail="TMDB returned no IMDB ID for this movie.")

    # 2. Check all systems
    plex_info = plex_svc.check_movie(imdb_id)
    sbx_info  = _check_seedbox(imdb_id, db)
    ipt_info  = _search_ipt(imdb_id)

    # 3. Determine status + upgrade flag
    status           = _determine_status(plex_info, sbx_info, ipt_info)
    upgrade_available = (
        plex_info["found"]
        and ipt_info.get("best") is not None
        and plex_info["resolution_rank"] < _res_rank(
            ipt_info["best"].get("resolution", "")
        )
    )

    # 4. Upsert movie_searches record
    _upsert_movie_search(db, details, plex_info, sbx_info, ipt_info, status)

    return {
        **details,
        "plex":             plex_info,
        "sbx":              sbx_info,
        "ipt":              ipt_info,
        "status":           status,
        "upgrade_available": upgrade_available,
    }


@router.get("/history")
def movie_history(db: Session = Depends(get_db)):
    """All searched movies, newest first."""
    rows = (
        db.query(MovieSearch)
        .order_by(MovieSearch.last_searched.desc())
        .all()
    )
    return [_serialize_search(r) for r in rows]


# ── Queue endpoints ───────────────────────────────────────────────────────────

@router.get("/queue")
def get_queue(db: Session = Depends(get_db)):
    """All movies currently in the watching queue."""
    rows = (
        db.query(MovieSearch)
        .filter(MovieSearch.queued == 1)
        .order_by(MovieSearch.last_searched.desc())
        .all()
    )
    return [_serialize_search(r) for r in rows]


@router.post("/queue/{imdb_id}", status_code=201)
def queue_movie(imdb_id: str, req: QueueRequest = None,
                db: Session = Depends(get_db)):
    """Add a movie to the watching queue."""
    movie = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
    if not movie:
        raise HTTPException(status_code=404,
                            detail="Movie not in search history — confirm it first.")
    min_res = (req.min_resolution if req else None) or "2160p"
    movie.queued        = 1
    movie.status        = "wanted"
    movie.queue_min_res = min_res
    db.commit()
    return {"ok": True, "imdb_id": imdb_id, "queue_min_res": min_res}


@router.delete("/queue/{imdb_id}")
def dequeue_movie(imdb_id: str, db: Session = Depends(get_db)):
    """Remove a movie from the watching queue."""
    movie = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
    if movie:
        movie.queued = 0
        if movie.status == "wanted":
            movie.status = "searched"
        db.commit()
    return {"ok": True}


# ── Upgrade review endpoints ──────────────────────────────────────────────────

@router.get("/reviews")
def get_reviews(db: Session = Depends(get_db)):
    """All pending upgrade reviews."""
    rows = (
        db.query(UpgradeReview)
        .filter(UpgradeReview.status == "pending")
        .order_by(UpgradeReview.created_at.desc())
        .all()
    )
    return [_serialize_review(r) for r in rows]


@router.post("/reviews/{review_id}/confirm")
def confirm_review(review_id: int, db: Session = Depends(get_db)):
    """Confirm upgrade: permanently delete the old trashed copy."""
    review = _get_review_or_404(review_id, db)
    try:
        if os.path.isdir(review.old_path):
            shutil.rmtree(review.old_path)
        elif os.path.isfile(review.old_path):
            os.remove(review.old_path)
        # Try to remove empty trash parent directories
        try:
            os.rmdir(os.path.dirname(review.old_path))
        except OSError:
            pass
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"Failed to delete old files: {exc}")
    review.status      = "confirmed"
    review.resolved_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/reviews/{review_id}/revert")
def revert_review(review_id: int, db: Session = Depends(get_db)):
    """
    Revert upgrade: restore old files from trash, delete new files.
    After revert, triggers a Plex refresh so the old copy is picked up.
    """
    review = _get_review_or_404(review_id, db)
    try:
        # Delete new files from new_path
        if os.path.isdir(review.new_path):
            for f in os.listdir(review.new_path):
                fpath = os.path.join(review.new_path, f)
                if os.path.isfile(fpath):
                    os.remove(fpath)
                elif os.path.isdir(fpath):
                    shutil.rmtree(fpath)

        # Restore old files from trash
        if os.path.isdir(review.old_path):
            for f in os.listdir(review.old_path):
                shutil.move(
                    os.path.join(review.old_path, f),
                    os.path.join(review.new_path, f),
                )
            try:
                os.rmdir(review.old_path)
                os.rmdir(os.path.dirname(review.old_path))  # .trash/
            except OSError:
                pass

        # Plex refresh for the restored path
        import threading
        threading.Thread(
            target=plex_svc.refresh_library_path,
            args=(review.new_path,),
            daemon=True,
        ).start()

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to revert: {exc}")

    review.status      = "reverted"
    review.resolved_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.get("/reviews/count")
def review_count(db: Session = Depends(get_db)):
    """Returns the number of pending upgrade reviews (for nav badge)."""
    count = db.query(UpgradeReview).filter(UpgradeReview.status == "pending").count()
    return {"count": count}


# ── Legacy endpoints (incoming-item TMDB matching) ────────────────────────────

@router.get("/match")
def get_match(category: str = Query(...), item: str = Query(...),
              db: Session = Depends(get_db)):
    match = (
        db.query(MovieMatch)
        .filter(MovieMatch.category == category, MovieMatch.item_name == item)
        .first()
    )
    query, year = clean_for_search(item)
    return {
        "match": _serialize_match(match) if match else None,
        "suggested_query": query,
        "suggested_year": year,
    }


@router.get("/matches")
def list_matches(category: str = Query(...), db: Session = Depends(get_db)):
    rows = db.query(MovieMatch).filter(MovieMatch.category == category).all()
    return {r.item_name: _serialize_match(r) for r in rows}


@router.post("/match", status_code=201)
def save_match(req: MatchRequest, db: Session = Depends(get_db)):
    existing = (
        db.query(MovieMatch)
        .filter(MovieMatch.category == req.category, MovieMatch.item_name == req.item_name)
        .first()
    )
    if existing:
        existing.tmdb_id        = req.tmdb_id
        existing.formatted_name = req.formatted_name
        existing.year           = req.year
        existing.poster_url     = req.poster_url
        existing.overview       = req.overview
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
    return _serialize_match(existing)


@router.delete("/match")
def clear_match(category: str = Query(...), item: str = Query(...),
                db: Session = Depends(get_db)):
    match = (
        db.query(MovieMatch)
        .filter(MovieMatch.category == category, MovieMatch.item_name == item)
        .first()
    )
    if match:
        db.delete(match)
        db.commit()
    return {"ok": True}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _res_from_title(title: str) -> str:
    m = _RES_RE.search(title)
    if not m:
        return "480p"
    val = m.group(1).lower()
    return "2160p" if val in ("4k", "uhd") else val


def _res_rank(res: str | None) -> int:
    if not res:
        return 0
    return _RES_RANK.get(str(res).lower().rstrip("p").replace("p", "") + "p", 0) or \
           _RES_RANK.get(str(res).lower(), 0)


def _check_seedbox(imdb_id: str, db: Session) -> dict:
    from ..services.sources.rtorrent import RtorrentSource
    rt = RtorrentSource()
    if not rt.is_configured():
        return {"configured": False, "found": False, "hash": None, "pct": None}

    movie = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
    stored_hash = movie.sbx_hash if movie else None

    try:
        brief = rt.list_all_brief()
        if stored_hash and stored_hash in brief:
            t = brief[stored_hash]
            return {
                "configured": True, "found": True,
                "hash": stored_hash, "pct": t.get("pct"),
                "name": t.get("name"),
            }
        return {"configured": True, "found": False, "hash": None, "pct": None}
    except Exception as exc:
        logger.warning(f"Seedbox check failed: {exc}")
        return {"configured": True, "found": False, "hash": None, "pct": None}


def _search_ipt(imdb_id: str) -> dict:
    from ..services.iptorrents import IPTorrentsClient
    ipt = IPTorrentsClient()
    if not ipt.is_configured():
        return {"configured": False, "results": [], "best": None, "best_resolution": None}
    try:
        results = ipt.search_by_imdb_id(imdb_id, category="movies")
        if not results:
            return {"configured": True, "results": [], "best": None, "best_resolution": None}

        serialized = [_serialize_ipt(r) for r in results]
        best = max(
            results,
            key=lambda r: (_RES_RANK.get(_res_from_title(r.title), 0), r.seeders),
        )
        best_res = _res_from_title(best.title)
        return {
            "configured":      True,
            "results":         serialized,
            "best":            _serialize_ipt(best),
            "best_resolution": best_res,
        }
    except Exception as exc:
        logger.warning(f"IPT search failed for {imdb_id}: {exc}")
        return {"configured": True, "results": [], "best": None, "best_resolution": None,
                "error": str(exc)}


def _determine_status(plex_info: dict, sbx_info: dict, ipt_info: dict) -> str:
    if plex_info["found"]:
        rank = plex_info.get("resolution_rank", 0)
        return "in_library" if rank >= 4 else "upgrading"
    if sbx_info.get("found"):
        return "grabbed"
    if ipt_info.get("results"):
        return "available"
    return "not_found"


def _upsert_movie_search(db: Session, details: dict, plex_info: dict,
                         sbx_info: dict, ipt_info: dict, status: str) -> None:
    imdb_id = details["imdb_id"]
    now     = datetime.utcnow()
    movie   = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
    if not movie:
        movie = MovieSearch(
            imdb_id      = imdb_id,
            first_searched = now,
        )
        db.add(movie)

    movie.tmdb_id        = details.get("tmdb_id")
    movie.title          = details["title"]
    movie.year           = details.get("year")
    movie.poster_url     = details.get("poster_url")
    movie.overview       = details.get("overview")
    movie.last_searched  = now
    movie.status         = status

    # Cache Plex result
    movie.plex_found      = 1 if plex_info.get("found") else 0
    movie.plex_resolution = plex_info.get("resolution")
    movie.plex_path       = plex_info.get("path")
    movie.plex_checked_at = now

    # Cache seedbox result
    if sbx_info.get("found"):
        movie.sbx_hash       = sbx_info.get("hash")
        movie.sbx_pct        = sbx_info.get("pct")
        movie.sbx_checked_at = now

    # Cache IPT result
    movie.ipt_best_res    = ipt_info.get("best_resolution")
    movie.ipt_checked_at  = now

    db.commit()


def _get_review_or_404(review_id: int, db: Session) -> UpgradeReview:
    review = db.query(UpgradeReview).filter(UpgradeReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found.")
    if review.status != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Review already {review.status}.")
    return review


def _serialize_search(m: MovieSearch) -> dict:
    return {
        "imdb_id":          m.imdb_id,
        "tmdb_id":          m.tmdb_id,
        "title":            m.title,
        "year":             m.year,
        "poster_url":       m.poster_url,
        "overview":         m.overview,
        "first_searched":   m.first_searched.isoformat() if m.first_searched else None,
        "last_searched":    m.last_searched.isoformat()  if m.last_searched  else None,
        "plex_found":       bool(m.plex_found),
        "plex_resolution":  m.plex_resolution,
        "plex_checked_at":  m.plex_checked_at.isoformat() if m.plex_checked_at else None,
        "sbx_hash":         m.sbx_hash,
        "sbx_pct":          m.sbx_pct,
        "ipt_best_res":     m.ipt_best_res,
        "status":           m.status,
        "queued":           bool(m.queued),
        "queue_min_res":    m.queue_min_res,
        "queue_check_count": m.queue_check_count,
    }


def _serialize_review(r: UpgradeReview) -> dict:
    def _fsize(path, fname):
        if not fname or not path:
            return None
        try:
            return os.path.getsize(os.path.join(path, fname))
        except OSError:
            return None

    return {
        "id":            r.id,
        "imdb_id":       r.imdb_id,
        "title":         r.title,
        "old_path":      r.old_path,
        "new_path":      r.new_path,
        "old_filename":  r.old_filename,
        "new_filename":  r.new_filename,
        "old_size_bytes": r.old_size_bytes or _fsize(r.old_path, r.old_filename),
        "new_size_bytes": r.new_size_bytes or _fsize(r.new_path, r.new_filename),
        "old_resolution": r.old_resolution,
        "new_resolution": r.new_resolution,
        "status":         r.status,
        "created_at":     r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_ipt(r) -> dict:
    return {
        "torrent_id":   r.torrent_id,
        "title":        r.title,
        "size_bytes":   r.size_bytes,
        "seeders":      r.seeders,
        "leechers":     r.leechers,
        "ipt_category": r.ipt_category,
        "torrent_url":  r.torrent_url,
        "info_url":     r.info_url,
        "pubdate":      r.pubdate,
        "resolution":   _res_from_title(r.title),
    }


def _serialize_match(m: MovieMatch) -> dict:
    return {
        "id":             m.id,
        "category":       m.category,
        "item_name":      m.item_name,
        "formatted_name": m.formatted_name,
        "tmdb_id":        m.tmdb_id,
        "year":           m.year,
        "poster_url":     m.poster_url,
        "overview":       m.overview,
    }
