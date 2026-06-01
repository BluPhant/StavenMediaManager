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
_RANK_RES: dict[int, str] = {4: "2160p", 3: "1440p", 2: "1080p", 1: "720p", 0: "480p"}
_RES_RE   = re.compile(r"\b(2160p|1440p|1080p|720p|480p|4[Kk]|UHD)\b", re.IGNORECASE)
_LOWQ_RE  = re.compile(
    r"\b(CAM|CAMRIP|HDCAM|TS|TELESYNC|TC|TELECINE|PDVD|SCR|SCREENER|DVDSCR)\b",
    re.IGNORECASE,
)

# Size scoring constants
_IDEAL_GB_PER_2HR = 15.0   # target for a 2-hour 2160p movie
_MAX_GB_PER_2HR   = 35.0   # above this = likely remux / overkill


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


@router.get("/plex-check")
def plex_check_single(tmdb_id: int):
    """
    Lightweight Plex status check for one TMDB ID.
    Used for lazy-loading badges on search-result cards.
    Makes one TMDB call to resolve the IMDB ID, then checks the in-memory
    Plex library cache (loaded once and shared across all calls).
    """
    if not settings.tmdb_api_key:
        return {"found": False, "resolution": None, "imdb_id": None, "resolution_rank": -1}
    details = get_tmdb_details(tmdb_id, settings.tmdb_api_key)
    if not details or not details.get("imdb_id"):
        return {"found": False, "resolution": None, "imdb_id": None, "resolution_rank": -1}
    imdb_id   = details["imdb_id"]
    plex_info = plex_svc.check_movie(imdb_id)
    return {
        "imdb_id":         imdb_id,
        "found":           plex_info.get("found", False),
        "resolution":      plex_info.get("resolution"),
        "resolution_rank": plex_info.get("resolution_rank", -1),
    }


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
    plex_info    = plex_svc.check_movie(imdb_id)
    sbx_info     = _check_seedbox(imdb_id, db)
    plex_rank    = plex_info.get("resolution_rank", -1) if plex_info.get("found") else -1
    runtime_min  = details.get("runtime") or 120
    ipt_info     = _search_ipt(imdb_id,
                               runtime_minutes=runtime_min,
                               current_plex_rank=plex_rank)

    # 3. Determine status + upgrade flag
    status            = _determine_status(plex_info, sbx_info, ipt_info)
    upgrade_available = (
        plex_info["found"]
        and ipt_info.get("best") is not None
        and plex_rank < _res_rank(ipt_info["best"].get("resolution", ""))
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
    """Add a movie to the watching queue and immediately fire an IPT check job."""
    movie = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
    if not movie:
        raise HTTPException(status_code=404,
                            detail="Movie not in search history — confirm it first.")
    min_res = (req.min_resolution if req else None) or "2160p"
    movie.queued        = 1
    movie.status        = "wanted"
    movie.queue_min_res = min_res
    db.commit()

    # Fire an immediate check so the user sees a job right away
    job_id = _submit_immediate_movie_check(imdb_id, movie.title, movie.year)
    return {"ok": True, "imdb_id": imdb_id, "queue_min_res": min_res, "job_id": job_id}


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


def _submit_immediate_movie_check(imdb_id: str, title: str,
                                   year: int | None) -> int | None:
    """Create a job and submit an immediate IPT check for a single queued movie."""
    from ..database import SessionLocal as _SL
    from ..models import Job as _Job
    from ..services.job_manager import submit_single_movie_check
    year_str = f" ({year})" if year else ""
    db2 = _SL()
    try:
        job = _Job(
            type="queue_check",
            category="movies",
            item_name=f"Check: {title}{year_str}",
            source_path="",
            status="pending",
            progress=0,
        )
        db2.add(job)
        db2.commit()
        db2.refresh(job)
        submit_single_movie_check(job.id, imdb_id)
        return job.id
    except Exception as exc:
        logger.warning(f"Immediate check job creation failed for {imdb_id}: {exc}")
        return None
    finally:
        db2.close()


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


def _size_score(size_bytes: int, runtime_minutes: int = 120) -> float:
    """
    Score a result by size fitness for the given runtime.
    1.0 = ideal (~15 GB for 2hr at 2160p), approaching 0 = too small or too large.
    Scales linearly with runtime so a 3-hr epic can be 22 GB and still score well.
    """
    if not size_bytes:
        return 0.5
    size_gb   = size_bytes / (1024 ** 3)
    scale     = max(runtime_minutes / 120.0, 0.5)
    ideal_gb  = _IDEAL_GB_PER_2HR * scale
    max_gb    = _MAX_GB_PER_2HR   * scale
    if size_gb <= ideal_gb:
        # Below ideal: light penalty for over-compression
        return max(0.35, 0.35 + 0.65 * (size_gb / ideal_gb))
    elif size_gb <= max_gb:
        # Above ideal but within range: linear penalty
        return 1.0 - 0.65 * (size_gb - ideal_gb) / (max_gb - ideal_gb)
    else:
        # Over max (remux / overkill): significant penalty
        return max(0.05, 0.35 - 0.30 * min((size_gb - max_gb) / max_gb, 1.0))


def _size_fitness_label(size_bytes: int, runtime_minutes: int = 120) -> str:
    """Human label for size fitness: ideal | ok | large | small | unknown."""
    if not size_bytes:
        return "unknown"
    size_gb  = size_bytes / (1024 ** 3)
    scale    = max(runtime_minutes / 120.0, 0.5)
    ideal_gb = _IDEAL_GB_PER_2HR * scale
    max_gb   = _MAX_GB_PER_2HR   * scale
    if size_gb > max_gb:
        return "large"
    elif size_gb > ideal_gb * 1.35:
        return "ok"
    elif size_gb >= ideal_gb * 0.35:
        return "ideal"
    else:
        return "small"


def _source_bonus(title: str) -> float:
    """Score the source type. WEB-DL preferred; REMUX penalised for size."""
    t = title.lower()
    if "remux" in t:
        return 0.4   # pristine quality but massive — penalised for size
    if "web-dl" in t or "webdl" in t:
        return 1.0
    if "blu-ray" in t or "bluray" in t or "bdrip" in t or "brrip" in t:
        return 0.9
    if "webrip" in t:
        return 0.8
    return 0.6


def _score_result(r, runtime_minutes: int) -> float:
    """
    Composite score used to rank IPT results.
    Weights: resolution (dominant) → size fitness → source type → seed count.
    """
    res_rank   = _RES_RANK.get(_res_from_title(r.title), 0)
    size_s     = _size_score(r.size_bytes, runtime_minutes)
    src_s      = _source_bonus(r.title)
    seeder_s   = min(r.seeders / 100.0, 1.0)
    return res_rank * 100 + size_s * 30 + src_s * 10 + seeder_s * 5


def _search_ipt(imdb_id: str, runtime_minutes: int = 120,
                current_plex_rank: int = -1) -> dict:
    """
    Search IPT by IMDB ID, score results, and return:
    - results:      quality-filtered & scored list (above current Plex quality if upgrading)
    - all_results:  full scored list (for "show all" toggle)
    - best:         top-scored result from the filtered set
    - filtered_by_quality: True when upgrade filter is active
    """
    from ..services.iptorrents import IPTorrentsClient
    ipt = IPTorrentsClient()
    _empty = {"configured": False, "results": [], "all_results": [], "best": None,
              "best_resolution": None, "filtered_by_quality": False,
              "current_plex_resolution": None, "runtime_minutes": runtime_minutes}
    if not ipt.is_configured():
        return _empty
    try:
        # Use resolution suffix in query when in Plex below 2160p — verified to
        # reduce IPT results from ~32 mixed to ~6 clean on-target results.
        res_param = "2160p" if 0 <= current_plex_rank < 4 else None
        raw = ipt.search_by_imdb_id(imdb_id, category="movies", resolution=res_param)
        # Strip CAM / TS / Screener (word-boundary safe — won't catch "BATS")
        raw = [r for r in raw if not _LOWQ_RE.search(r.title)]
        if not raw:
            return {**_empty, "configured": True}

        # Sort by composite score descending
        scored = sorted(raw, key=lambda r: _score_result(r, runtime_minutes), reverse=True)

        # Quality filter: when upgrading, only show results strictly above current Plex tier
        upgrading = current_plex_rank >= 0 and current_plex_rank < 4
        if upgrading:
            filtered = [r for r in scored
                        if _RES_RANK.get(_res_from_title(r.title), 0) > current_plex_rank]
        else:
            filtered = scored

        best_pool = filtered if filtered else scored
        best      = best_pool[0] if best_pool else None

        return {
            "configured":              True,
            "results":                 [_serialize_ipt(r, runtime_minutes) for r in filtered],
            "all_results":             [_serialize_ipt(r, runtime_minutes) for r in scored],
            "best":                    _serialize_ipt(best, runtime_minutes) if best else None,
            "best_resolution":         _res_from_title(best.title) if best else None,
            "filtered_by_quality":     upgrading and bool(filtered),
            "current_plex_resolution": _RANK_RES.get(current_plex_rank) if upgrading else None,
            "runtime_minutes":         runtime_minutes,
        }
    except Exception as exc:
        logger.warning(f"IPT search failed for {imdb_id}: {exc}")
        return {**_empty, "configured": True, "error": str(exc)}


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


def _serialize_ipt(r, runtime_minutes: int = 120) -> dict:
    size_gb      = (r.size_bytes / (1024 ** 3)) if r.size_bytes else 0.0
    runtime_hrs  = max(runtime_minutes / 60.0, 0.5)
    gb_per_hour  = size_gb / runtime_hrs if size_gb else 0.0
    res          = _res_from_title(r.title)
    return {
        "torrent_id":   r.torrent_id,
        "title":        r.title,
        "size_bytes":   r.size_bytes,
        "size_gb":      round(size_gb, 1),
        "gb_per_hour":  round(gb_per_hour, 1),
        "size_fitness": _size_fitness_label(r.size_bytes, runtime_minutes),
        "seeders":      r.seeders,
        "leechers":     r.leechers,
        "ipt_category": r.ipt_category,
        "torrent_url":  r.torrent_url,
        "info_url":     r.info_url,
        "pubdate":      r.pubdate,
        "resolution":   res,
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
