"""
Queue checker — periodically checks IPT for queued movies and auto-grabs them.

Called as a job (type="queue_check") by the scheduler in job_manager.
Visible in the Jobs panel while running; self-reschedules via the scheduler daemon.
"""
import logging
import re
from datetime import datetime

from ..config import settings
from ..database import SessionLocal
from ..models import MovieMatch, MovieSearch
from .job_manager import update_job
from .iptorrents import IPTorrentsClient
from .sources.rtorrent import RtorrentSource, extract_info_hash

logger = logging.getLogger(__name__)

# Resolution tier ranking for auto-grab decisions
_RES_RANK: dict[str, int] = {
    "2160p": 4, "4k": 4,
    "1440p": 3,
    "1080p": 2,
    "720p": 1,
    "480p": 0,
}


def run_queue_check(job_id: int) -> None:
    """
    Check all queued movies against IPT.  Auto-grabs if a result meets the
    minimum resolution threshold stored per movie.

    Runs as a background job submitted by the scheduler daemon every 4 hours.
    """
    update_job(job_id, status="running", progress=5,
               message="Loading queue…")

    db = SessionLocal()
    try:
        queued = db.query(MovieSearch).filter(MovieSearch.queued == 1).all()
    finally:
        db.close()

    if not queued:
        update_job(job_id, status="done", progress=100,
                   message="Queue is empty — nothing to check.")
        return

    ipt = IPTorrentsClient()
    if not ipt.is_configured():
        update_job(job_id, status="error",
                   message="IPTorrents not configured — cannot check queue.")
        return

    rt = RtorrentSource()
    if not rt.is_configured():
        update_job(job_id, status="error",
                   message="rTorrent not configured — cannot grab from queue.")
        return

    total   = len(queued)
    grabbed = 0
    errors  = []

    for idx, movie in enumerate(queued):
        pct_base = 5 + int(idx / total * 90)
        update_job(job_id, progress=pct_base,
                   message=f"[{idx+1}/{total}] Checking {movie.title} ({movie.year})…")
        try:
            results = ipt.search_by_imdb_id(movie.imdb_id, category="movies")
            if not results:
                _bump_check_count(movie.imdb_id)
                continue

            # Pick best result at or above the queue's minimum resolution
            min_rank = _RES_RANK.get((movie.queue_min_res or "2160p").lower(), 4)
            best = _pick_best(results, min_rank)
            if not best:
                _bump_check_count(movie.imdb_id)
                logger.info(
                    f"Queue: {movie.title} — no result at "
                    f">={movie.queue_min_res or '2160p'} yet"
                )
                continue

            # Auto-grab
            logger.info(f"Queue: grabbing {movie.title} ({best.title})")
            torrent_bytes = ipt.fetch_torrent_bytes(best.torrent_url)
            info_hash     = extract_info_hash(torrent_bytes)
            label         = settings.rtorrent_tag
            rt.load_torrent(torrent_bytes, label=label)

            # Update movie record
            _mark_grabbed(movie.imdb_id, info_hash)

            # Also ensure a MovieMatch exists so auto-move works on sync
            if settings.tmdb_api_key:
                from .movies import auto_match_movie
                auto_match_movie(best.title, settings.tmdb_api_key, None)

            grabbed += 1
            update_job(job_id, progress=pct_base + 5,
                       message=f"Grabbed: {movie.title} — {best.title}")

        except Exception as exc:
            logger.error(f"Queue check failed for {movie.imdb_id}: {exc}", exc_info=True)
            errors.append(f"{movie.title}: {exc}")

    summary = f"Queue check done. {grabbed}/{total} grabbed."
    if errors:
        summary += f" Errors: {'; '.join(errors)}"
    update_job(job_id, status="done", progress=100, message=summary)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _res_from_title(title: str) -> str:
    """Extract resolution token from a torrent title, e.g. '2160p', '1080p'."""
    m = re.search(r"\b(2160p|1440p|1080p|720p|480p|4K|UHD)\b", title, re.IGNORECASE)
    if not m:
        return "480p"
    val = m.group(1).lower()
    return "2160p" if val in ("4k", "uhd") else val


def _pick_best(results, min_rank: int):
    """Return the best result at or above min_rank (highest rank, then most seeds)."""
    eligible = [
        r for r in results
        if _RES_RANK.get(_res_from_title(r.title), 0) >= min_rank
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda r: (
        _RES_RANK.get(_res_from_title(r.title), 0),
        r.seeders,
    ))


def _bump_check_count(imdb_id: str) -> None:
    db = SessionLocal()
    try:
        m = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
        if m:
            m.queue_check_count = (m.queue_check_count or 0) + 1
            m.queue_checked_at  = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _mark_grabbed(imdb_id: str, info_hash: str) -> None:
    db = SessionLocal()
    try:
        m = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
        if m:
            m.sbx_hash          = info_hash
            m.sbx_pct           = 0
            m.sbx_checked_at    = datetime.utcnow()
            m.queued            = 0
            m.status            = "grabbed"
            m.queue_check_count = (m.queue_check_count or 0) + 1
            m.queue_checked_at  = datetime.utcnow()
            db.commit()
    finally:
        db.close()
