import json
import logging
import os
import shutil
import urllib.request

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
    "games":        "games",
    "switch games": "switch-games",
    "pc games":     "pc-games",
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


def run_move(job_id: int, source_path: str, formatted_name: str, category: str) -> None:
    update_job(job_id, status="running", progress=5, message="Preparing destination...")

    subdir   = dest_subdir(category)
    dest_dir = os.path.join(settings.media_dir, subdir, formatted_name)

    try:
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
        except OSError:
            pass

        plex_note = _try_plex_refresh(category)

        update_job(
            job_id,
            status="done",
            progress=100,
            dest_path=dest_dir,
            message=f"Moved to {dest_dir}.{plex_note}",
        )

    except Exception as exc:
        update_job(job_id, status="error", message=str(exc))
        raise


# ── Plex refresh (best-effort, never fails the job) ──────────────────────────

def _try_plex_refresh(category: str) -> str:
    if not (settings.plex_url and settings.plex_token):
        return ""
    try:
        section_id = _plex_section_id(category)
        base  = settings.plex_url.rstrip("/")
        token = settings.plex_token
        path  = f"/library/sections/{section_id}/refresh" if section_id else "/library/sections/all/refresh"
        url   = f"{base}{path}?X-Plex-Token={token}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10):  # noqa: S310
            pass
        return " Plex refreshed."
    except Exception as exc:
        logger.warning(f"Plex refresh failed (non-fatal): {exc}")
        return " (Plex refresh failed.)"


def _plex_section_id(category: str) -> str | None:
    plex_type = PLEX_TYPE_MAP.get(category.lower())
    if not plex_type:
        return None
    base  = settings.plex_url.rstrip("/")
    token = settings.plex_token
    url   = f"{base}/library/sections?X-Plex-Token={token}"
    req   = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode())
    for section in data.get("MediaContainer", {}).get("Directory", []):
        if section.get("type") == plex_type:
            return str(section["key"])
    return None
