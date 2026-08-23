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
from .sources import get_active_source
from .sources.rtorrent import extract_info_hash

logger = logging.getLogger(__name__)

# Resolution tier ranking for auto-grab decisions
_RES_RANK: dict[str, int] = {
    "2160p": 4, "4k": 4,
    "1440p": 3,
    "1080p": 2,
    "720p": 1,
    "480p": 0,
}

# Exclude CAM / TS / Screener releases (word-boundary match so e.g. "BATS" is safe)
_LOWQ_RE = re.compile(
    r"\b(CAM|CAMRIP|HDCAM|HDTS|HDTC|TS|TELESYNC|TC|TELECINE|PDVD|SCR|SCREENER|DVDSCR)\b",
    re.IGNORECASE,
)
# Exclude YIFY/YTS — aggressively compressed, wrong for 4K libraries
_YIFY_RE = re.compile(r"\b(YIFY|YTS(\.[A-Z]{2,4})?)\b", re.IGNORECASE)


def run_single_movie_check(job_id: int, imdb_id: str) -> None:
    """
    Immediate IPT check for a single movie — runs when the user first queues it.
    Uses q=tt1234567+2160p when the target is 2160p (verified to work on IPT).
    """
    db = SessionLocal()
    try:
        movie = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
        if not movie:
            update_job(job_id, status="error", message=f"Movie {imdb_id} not found.")
            return
        title    = movie.title
        year     = movie.year
        min_res  = movie.queue_min_res or "2160p"
        min_rank = _RES_RANK.get(min_res.lower(), 4)
    finally:
        db.close()

    title_str = f"{title} ({year})" if year else title
    update_job(job_id, status="running", progress=15, message=f"Checking IPT for {title_str}…")

    ipt = IPTorrentsClient()
    if not ipt.is_configured():
        update_job(job_id, status="done", progress=100,
                   message=f"Queued: {title_str}. IPT not configured.")
        return

    source = get_active_source()

    try:
        # Append resolution to query when targeting 2160p — verified to cut results
        # from ~32 mixed to ~6 clean 2160p-only results on IPT.
        res_param = min_res if min_rank >= 4 else None
        results   = ipt.search_by_imdb_id(imdb_id, category="movies", resolution=res_param)
        results   = [r for r in results if not _YIFY_RE.search(r.title)]
        results   = [r for r in results if not _LOWQ_RE.search(r.title)]

        if not results:
            update_job(job_id, status="done", progress=100,
                       message=f"Not on IPT yet at {min_res} — {title_str} is on the watch list.")
            return

        best = _pick_best(results, min_rank)
        if not best:
            top_res = _res_from_title(results[0].title)
            update_job(job_id, status="done", progress=100,
                       message=f"Found on IPT but not at {min_res} (best: {top_res}). "
                               f"{title_str} stays on watch list.")
            return

        if not source:
            update_job(job_id, status="done", progress=100,
                       message=f"Found {_res_from_title(best.title)} on IPT but no seedbox "
                               f"configured — {title_str} stays on watch list.")
            return

        update_job(job_id, progress=60, message=f"Grabbing {best.title[:65]}…")
        torrent_bytes = ipt.fetch_torrent_bytes(best.torrent_url)
        info_hash     = extract_info_hash(torrent_bytes)
        source.load_torrent(torrent_bytes, label=source.default_category)
        _mark_grabbed(imdb_id, info_hash)

        if settings.tmdb_api_key:
            from .movies import auto_match_movie
            auto_match_movie(best.title, settings.tmdb_api_key, None)

        update_job(job_id, status="done", progress=100,
                   message=f"Grabbed: {best.title[:70]}")

    except Exception as exc:
        logger.error(f"Single check failed for {imdb_id}: {exc}", exc_info=True)
        update_job(job_id, status="error", message=f"Check failed: {exc}")


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

    source = get_active_source()
    if not source:
        update_job(job_id, status="error",
                   message="No seedbox source configured — cannot grab from queue.")
        return

    total   = len(queued)
    grabbed = 0
    errors  = []

    for idx, movie in enumerate(queued):
        pct_base = 5 + int(idx / total * 90)
        update_job(job_id, progress=pct_base,
                   message=f"[{idx+1}/{total}] Checking {movie.title} ({movie.year})…")
        try:
            min_res  = movie.queue_min_res or "2160p"
            min_rank = _RES_RANK.get(min_res.lower(), 4)
            res_param = min_res if min_rank >= 4 else None
            results = ipt.search_by_imdb_id(movie.imdb_id, category="movies",
                                             resolution=res_param)
            results = [r for r in results if not _YIFY_RE.search(r.title)]
            results = [r for r in results if not _LOWQ_RE.search(r.title)]
            if not results:
                _bump_check_count(movie.imdb_id)
                continue

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
            source.load_torrent(torrent_bytes, label=source.default_category)

            # Update movie record
            _mark_grabbed(movie.imdb_id, info_hash)

            # Create MovieMatch from the known MovieSearch data (not auto_match_movie
            # which parses the torrent title and often fails on dirty names)
            try:
                from .sources.rtorrent import extract_torrent_name
                torrent_name = extract_torrent_name(torrent_bytes)
            except Exception:
                torrent_name = best.title
            _ensure_movie_match(torrent_name, movie)

            # Notify sync scheduler to start fast-polling
            from .job_manager import notify_grab
            notify_grab()

            grabbed += 1
            update_job(job_id, progress=pct_base + 5,
                       message=f"Grabbed: {movie.title} — {best.title}")

        except Exception as exc:
            logger.error(f"Queue check failed for {movie.imdb_id}: {exc}", exc_info=True)
            errors.append(f"{movie.title}: {exc}")

    if grabbed == 0 and not errors:
        # No-op run — delete the job record to keep the panel clean
        db2 = SessionLocal()
        try:
            from ..models import Job
            db2.query(Job).filter(Job.id == job_id).delete()
            db2.commit()
        finally:
            db2.close()
        logger.info(f"Queue check done (0/{total} grabbed) — job record removed.")
        return

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


def _ensure_movie_match(torrent_name: str, movie: "MovieSearch") -> None:
    """Create MovieMatch directly from the MovieSearch record we already have."""
    from ..models import MovieMatch
    from .movies import get_tmdb_details

    db = SessionLocal()
    try:
        existing = db.query(MovieMatch).filter(
            MovieMatch.category == "movies",
            MovieMatch.item_name == torrent_name,
        ).first()
        if existing:
            return

        formatted = f"{movie.title} ({movie.year})" if movie.year else movie.title
        imdb_id = movie.imdb_id
        tmdb_id = movie.tmdb_id
        poster = movie.poster_url
        overview = movie.overview

        if tmdb_id and settings.tmdb_api_key:
            try:
                details = get_tmdb_details(tmdb_id, settings.tmdb_api_key)
                if details:
                    formatted = details.get("formatted_name", formatted)
                    poster = details.get("poster_url") or poster
                    overview = details.get("overview") or overview
            except Exception:
                pass

        m = MovieMatch(
            category="movies",
            item_name=torrent_name,
            tmdb_id=tmdb_id or 0,
            imdb_id=imdb_id,
            formatted_name=formatted,
            year=movie.year,
            poster_url=poster,
            overview=overview,
        )
        db.add(m)
        db.commit()
        logger.info(f"MovieMatch created (queue grab): '{torrent_name}' -> '{formatted}'")
    except Exception as exc:
        logger.warning(f"MovieMatch creation failed for '{torrent_name}': {exc}")
    finally:
        db.close()
