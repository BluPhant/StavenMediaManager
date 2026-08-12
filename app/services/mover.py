import json
import logging
import os
import re
import shutil
import threading
import urllib.request
from datetime import datetime

from ..config import settings
from .job_manager import update_job

logger = logging.getLogger(__name__)

# Category name (lowercase) → subdirectory under /media
# Extend this as new category workflows are added
DEST_MAP: dict[str, str] = {
    "movies":       "movies",
    "movie":        "movies",
    "tv":           "tv",
    "tv shows":     "tv",
    "television":   "tv",
    "audiobooks":   "audiobooks",
    "audiobook":    "audiobooks",
    "music":        "music",
    "books":        "books",
    "ebooks":       "ebooks",
    "games":         "games",
    "switch games":  "games/switch",
    "switch-games":  "games/switch",
    "pc games":      "pc-games",
}

# Plex section type by category name
PLEX_TYPE_MAP: dict[str, str] = {
    "movies": "movie",
    "movie":  "movie",
    "tv":     "show",
    "tv shows": "show",
    "music":  "artist",
}


def dest_subdir(category: str) -> str:
    return DEST_MAP.get(category.lower(), category.lower())


def run_switch_move(job_id: int, source_path: str, title_id: int,
                    content_id: int, dest_dir: str) -> None:
    """
    Move a Switch game / update / DLC into the library folder.
    Unlike movies, we accumulate — files are added to dest_dir, never replaced.
    After the move, SwitchContent.library_path and SwitchTitle.library_path are updated.
    """
    update_job(job_id, status="running", progress=5, message="Preparing Switch library folder…")

    if not os.path.isdir(source_path):
        update_job(job_id, status="error",
                   message="Source directory gone (duplicate job?)")
        return

    try:
        os.makedirs(dest_dir, exist_ok=True)
        entries = [e for e in os.listdir(source_path)
                   if not e.lower().endswith((".nfo", ".sfv", ".txt"))]
        total = max(len(entries), 1)
        update_job(job_id, progress=10, message=f"Moving {total} file(s) into library…")

        moved_game_file = None
        for idx, name in enumerate(entries, 1):
            src = os.path.join(source_path, name)
            dst = os.path.join(dest_dir, name)
            # Skip if destination already has this file (idempotent)
            if os.path.exists(dst):
                logger.info(f"Switch move: skipping {name} — already at destination")
            else:
                shutil.move(src, dst)
                if os.path.splitext(name)[1].lower() in (".xci", ".nsp", ".nsz"):
                    moved_game_file = name
            update_job(job_id, progress=10 + int(idx / total * 80), message=f"Moved {name}")

        # Remove leftover scene files (.nfo/.sfv/.txt) then the now-empty folder
        _SCENE_EXTS = {".nfo", ".sfv", ".txt"}
        try:
            for name in os.listdir(source_path):
                if os.path.splitext(name)[1].lower() in _SCENE_EXTS:
                    try:
                        os.remove(os.path.join(source_path, name))
                    except OSError:
                        pass
            os.rmdir(source_path)
        except OSError:
            pass

        _fix_permissions(dest_dir)

        # Update DB records + write metadata to library folder
        from ..database import SessionLocal
        from ..models import SwitchContent, SwitchTitle
        db = SessionLocal()
        try:
            content = db.query(SwitchContent).filter(SwitchContent.id == content_id).first()
            if content:
                if moved_game_file:
                    content.filename = moved_game_file
                    content.library_path = os.path.join(dest_dir, moved_game_file)
                    try:
                        content.file_size = os.path.getsize(content.library_path)
                    except OSError:
                        pass
            t = db.query(SwitchTitle).filter(SwitchTitle.id == title_id).first()
            if t:
                t.library_path = dest_dir
                _write_switch_metadata(dest_dir, t, db)
            db.commit()
        finally:
            db.close()

        update_job(job_id, status="done", progress=100, dest_path=dest_dir,
                   message=f"Switch content moved to {dest_dir}")

    except Exception as exc:
        update_job(job_id, status="error", message=str(exc))
        raise


def run_move(job_id: int, source_path: str, formatted_name: str,
             category: str, imdb_id: str = "") -> None:
    update_job(job_id, status="running", progress=5, message="Preparing destination...")

    subdir   = dest_subdir(category)
    dest_dir = os.path.join(settings.media_dir, subdir, formatted_name)

    try:
        # Bail immediately if source is already gone (duplicate job)
        if not os.path.isdir(source_path):
            update_job(job_id, status="error",
                       message=f"Source directory gone (likely already moved by another job).")
            return

        # ── Upgrade detection ─────────────────────────────────────────────────
        # Find any existing copy of this movie before creating dest_dir.
        # Priority: plex_path from DB (reliable even when folder names differ,
        # e.g. "National Lampoons Vacation" vs "National Lampoon's Vacation")
        # → fallback: check dest_dir itself (covers same-name moves).
        is_movie = category.lower() in ("movies", "movie")
        upgrade_review_id = None
        if is_movie:
            old_dir = None
            if imdb_id:
                old_dir = _find_existing_movie_folder(imdb_id)
            if not old_dir and os.path.isdir(dest_dir):
                old_dir = dest_dir
            if old_dir and os.path.isdir(old_dir):
                video_files = [f for f in os.listdir(old_dir) if _is_video(f)]
                if video_files:
                    update_job(job_id, progress=6,
                               message=f"Existing copy found — moving to trash…")
                    upgrade_review_id = _trash_old_copy(
                        old_dir, formatted_name, imdb_id, video_files
                    )

        os.makedirs(dest_dir, exist_ok=True)

        entries = os.listdir(source_path)
        total   = max(len(entries), 1)

        update_job(job_id, progress=10, message=f"Moving {total} item(s)...")

        for idx, name in enumerate(entries, 1):
            shutil.move(os.path.join(source_path, name), os.path.join(dest_dir, name))
            update_job(
                job_id,
                progress=10 + int(idx / total * 80),
                message=f"Moved {name}",
            )

        # Remove now-empty source directory (best-effort)
        try:
            os.rmdir(source_path)
            # If this was a bundle sub-item (e.g. "Full Cast/Book 1"), also clean up
            # the parent bundle folder once it's empty. Guard against removing the
            # category root itself.
            parent = os.path.dirname(source_path)
            category_dir = os.path.join(settings.incoming_dir, category)
            if os.path.normpath(parent) != os.path.normpath(category_dir):
                try:
                    os.rmdir(parent)
                    logger.info("Removed empty bundle folder: %s", parent)
                except OSError:
                    pass
        except OSError:
            pass

        _fix_permissions(dest_dir)

        # Update UpgradeReview with new file info (now files are in dest_dir)
        if upgrade_review_id:
            _update_review_new_file(upgrade_review_id, dest_dir)

        # Update MovieSearch record so discover page reflects the move
        if is_movie and imdb_id:
            _update_movie_search_after_move(imdb_id, dest_dir)

        # Plex targeted refresh runs async — never delays job completion
        threading.Thread(
            target=_try_plex_refresh, args=(category, dest_dir), daemon=True
        ).start()

        suffix = " Upgrade review pending." if upgrade_review_id else ""
        update_job(
            job_id,
            status="done",
            progress=100,
            dest_path=dest_dir,
            message=f"Moved to {dest_dir}. Plex refresh queued.{suffix}",
        )

    except Exception as exc:
        update_job(job_id, status="error", message=str(exc))
        raise


# ── Plex refresh (best-effort, fire-and-forget from caller) ──────────────────

def _try_plex_refresh(category: str, dest_path: str | None = None) -> None:
    """Run in a background thread — never blocks the move job."""
    if category.lower() in ("movies", "movie"):
        from . import plex as plex_svc
        plex_svc.refresh_library_path(dest_path)
    elif settings.plex_url and settings.plex_token:
        # Non-movie categories: trigger a full section refresh (existing behaviour)
        try:
            plex_type  = PLEX_TYPE_MAP.get(category.lower())
            base  = settings.plex_url.rstrip("/")
            token = settings.plex_token
            if plex_type:
                from . import plex as plex_svc
                sid = plex_svc.get_section_id_for_movies() if plex_type == "movie" else None
                # For non-movie types fall back to sections/all
                url = (
                    f"{base}/library/sections/{sid}/refresh?X-Plex-Token={token}"
                    if sid else
                    f"{base}/library/sections/all/refresh?X-Plex-Token={token}"
                )
            else:
                url = f"{base}/library/sections/all/refresh?X-Plex-Token={token}"
            with urllib.request.urlopen(urllib.request.Request(url), timeout=10):  # noqa: S310
                pass
            logger.info(f"Plex refresh triggered for category '{category}'")
        except Exception as exc:
            logger.warning(f"Plex refresh failed (non-fatal): {exc}")


# ── Upgrade helpers ───────────────────────────────────────────────────────────

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".iso"}
_RES_RE     = re.compile(r"\b(2160p|1440p|1080p|720p|480p|4[Kk]|UHD)\b", re.IGNORECASE)


def _is_video(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in _VIDEO_EXTS


def _extract_res(name: str) -> str | None:
    m = _RES_RE.search(name)
    if not m:
        return None
    val = m.group(1).lower()
    return "2160p" if val in ("4k", "uhd") else val


def _main_video_file(folder: str) -> tuple[str | None, int]:
    """Return (filename, size_bytes) of the largest video file in folder."""
    best_name, best_size = None, 0
    try:
        for f in os.listdir(folder):
            if _is_video(f):
                try:
                    sz = os.path.getsize(os.path.join(folder, f))
                    if sz > best_size:
                        best_name, best_size = f, sz
                except OSError:
                    pass
    except OSError:
        pass
    return best_name, best_size


def _trash_old_copy(old_dir: str, formatted_name: str,
                    imdb_id: str, video_files: list[str]) -> int | None:
    """
    Move all files from old_dir to the .trash subfolder and remove the
    (now-empty) old_dir.  Creates an UpgradeReview DB record.
    Returns review ID or None on error.
    old_dir may differ from the new dest_dir when a folder was named
    differently (e.g. apostrophe vs no apostrophe).
    """
    subdir    = "movies"   # only called for movie category
    trash_dir = os.path.join(settings.media_dir, subdir, ".trash", formatted_name)
    try:
        os.makedirs(trash_dir, exist_ok=True)
        for name in os.listdir(old_dir):
            shutil.move(os.path.join(old_dir, name), os.path.join(trash_dir, name))
        # Remove the now-empty old folder (may have a different name than dest_dir)
        try:
            os.rmdir(old_dir)
        except OSError:
            pass

        old_fname, old_size = _main_video_file(trash_dir)
        old_res = _extract_res(old_fname or "")

        from ..database import SessionLocal
        from ..models import UpgradeReview
        db = SessionLocal()
        try:
            review = UpgradeReview(
                imdb_id      = imdb_id or None,
                title        = formatted_name,
                old_path     = trash_dir,
                new_path     = None,
                old_filename = old_fname,
                new_filename = None,    # filled in after new files land
                old_size_bytes = old_size or None,
                new_size_bytes = None,
                old_resolution = old_res,
                new_resolution = None,
            )
            db.add(review)
            db.commit()
            db.refresh(review)
            logger.info(
                f"Upgrade review created: id={review.id} "
                f"'{formatted_name}' old={old_res}"
            )
            return review.id
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"Failed to trash old copy for '{formatted_name}': {exc}")
        return None


def _update_review_new_file(review_id: int, dest_dir: str) -> None:
    """After new files land in dest_dir, fill in new_filename/size/resolution."""
    from ..database import SessionLocal
    from ..models import UpgradeReview
    db = SessionLocal()
    try:
        review = db.query(UpgradeReview).filter(UpgradeReview.id == review_id).first()
        if not review:
            return
        new_fname, new_size = _main_video_file(dest_dir)
        review.new_filename   = new_fname
        review.new_size_bytes = new_size or None
        review.new_resolution = _extract_res(new_fname or "")
        db.commit()
        logger.info(
            f"Upgrade review {review_id} updated: "
            f"new={review.new_resolution} ({new_fname})"
        )
    finally:
        db.close()


def _fix_permissions(directory: str) -> None:
    """Set 755 on dirs and 644 on files so Plex and other processes can access them."""
    try:
        os.chmod(directory, 0o755)
        for root, dirs, files in os.walk(directory):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o644)
                except OSError:
                    pass
    except Exception as exc:
        logger.warning(f"Permission fix failed (non-fatal): {exc}")


# ── Plex-path translation ─────────────────────────────────────────────────────

def _update_movie_search_after_move(imdb_id: str, dest_dir: str) -> None:
    """After a successful move, update the MovieSearch record so the discover page
    reflects that this movie is now in the library."""
    from ..database import SessionLocal
    from ..models import MovieSearch
    db = SessionLocal()
    try:
        record = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
        if record:
            record.status = "in_library"
            # Find the main video file to set plex_path (Plex will report something similar)
            video = next((f for f in os.listdir(dest_dir) if _is_video(f)), None)
            if video:
                record.plex_path = os.path.join(dest_dir, video)
            db.commit()
            logger.info(f"MovieSearch {imdb_id} updated: status=in_library")
    except Exception as exc:
        logger.warning(f"Failed to update MovieSearch after move for {imdb_id}: {exc}")
    finally:
        db.close()


def _find_existing_movie_folder(imdb_id: str) -> str | None:
    """
    Look up the Plex-reported file path for a movie stored in movie_searches and
    translate it to a local folder path.  Returns None if not found or the folder
    no longer exists on disk.
    """
    from ..database import SessionLocal
    from ..models import MovieSearch
    db = SessionLocal()
    try:
        record = db.query(MovieSearch).filter(MovieSearch.imdb_id == imdb_id).first()
        if not record or not record.plex_path:
            return None
        return _translate_plex_path(record.plex_path)
    except Exception as exc:
        logger.warning(f"Could not look up existing folder for {imdb_id}: {exc}")
        return None
    finally:
        db.close()


def _write_switch_metadata(dest_dir: str, title, db) -> None:
    """Write cover.jpg and metadata.json into the Switch game library folder."""
    # cover.jpg
    cover_dest = os.path.join(dest_dir, "cover.jpg")
    if not os.path.exists(cover_dest):
        if title.cover_local and os.path.exists(title.cover_local):
            try:
                shutil.copy2(title.cover_local, cover_dest)
            except OSError as exc:
                logger.warning(f"Cover copy failed: {exc}")
        elif title.cover_url:
            try:
                req = urllib.request.Request(
                    title.cover_url, headers={"User-Agent": "StavenMediaManager/1.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        with open(cover_dest, "wb") as f:
                            f.write(resp.read())
            except Exception as exc:
                logger.warning(f"Cover download to library failed: {exc}")

    # Point cover_local at the library copy
    if os.path.exists(cover_dest):
        title.cover_local = cover_dest

    # metadata.json
    meta_dest = os.path.join(dest_dir, "metadata.json")
    try:
        metadata = {
            "title":       title.title,
            "game_id":     title.game_id,
            "igdb_id":     title.igdb_id,
            "nintendo_id": title.nintendo_id,
            "developer":   title.developer,
            "publisher":   title.publisher,
            "cover_url":   title.cover_url,
        }
        with open(meta_dest, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning(f"metadata.json write failed: {exc}")


def _translate_plex_path(plex_file_path: str) -> str | None:
    """
    Convert a Plex-reported file path to a local filesystem folder path.

    Plex mounts the media share under its own prefix (e.g. /data/) while SMM
    uses settings.media_dir (/media/).  We find the known category subdirectory
    in the Plex path (movies, tv, music, …) and rebase everything from that
    point onto settings.media_dir.  Returns the parent directory (the movie
    folder), not the file itself.

    Example:
        /data/movies/National Lampoons Vacation (1983)/file.mkv
        → /media/movies/National Lampoons Vacation (1983)
    """
    norm  = plex_file_path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    for subdir in set(DEST_MAP.values()):
        if subdir in parts:
            idx        = parts.index(subdir)
            local_path = os.path.join(settings.media_dir, *parts[idx:])
            folder     = os.path.dirname(local_path)
            return folder if os.path.isdir(folder) else None
    return None
