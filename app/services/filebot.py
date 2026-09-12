"""
FileBot TV organize service.

Runs  docker exec FileBot /opt/filebot/filebot -rename ...
to rename and move TV episode files from Incoming into the library.

Volume mapping assumption:
  app container  /media/...  ==  FileBot container /storage/...
  (both mount the same Unraid media-files share)

Docker socket must be mounted into the app container for `docker exec` to work:
  /var/run/docker.sock:/var/run/docker.sock  (add to Unraid template if missing)
"""
import logging
import os
import re
import subprocess

from ..config import settings

logger = logging.getLogger(__name__)

_NOBODY_UID = 99
_NOBODY_GID = 100

FILEBOT_CONTAINER = "FileBot"
FILEBOT_BIN = "/opt/filebot/filebot"
_STORAGE_ROOT = "/storage"  # FileBot container mount point for the media share


def _to_storage_path(app_path: str) -> str:
    """
    Convert an absolute path in the app container (/media/...) to the
    equivalent path in the FileBot container (/storage/...).
    """
    rel = os.path.relpath(app_path, settings.media_dir)
    return (_STORAGE_ROOT + "/" + rel.replace("\\", "/")).rstrip("/")


def _normalize(name: str) -> str:
    """Lowercase, strip non-alphanumeric — used for fuzzy folder matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_existing_tv_folder(incoming_name: str) -> str | None:
    """
    Scan the TV library for an existing series folder that fuzzy-matches the
    incoming Incoming folder name.  Returns the library folder name (not full
    path) on a match, or None.

    Matching strategy:
      - Strip trailing season/episode info from the incoming name
        (e.g. "American.Dad.S19E01.720p" → "american dad")
      - Strip the "{tmdb-XXXX}" suffix from library folder names before comparing
      - Accept if one normalized name is a prefix of the other
    """
    tv_dir = os.path.join(settings.media_dir, "tv")
    if not os.path.isdir(tv_dir):
        return None

    # Strip trailing season/ep markers and release tags from incoming name
    stripped = re.sub(r"[\.\s_-]+[Ss]\d+.*$", "", incoming_name)
    stripped = re.sub(r"[\.\s_-]+\d{4}.*$", "", stripped)  # also strip trailing year+stuff
    norm_incoming = _normalize(stripped) or _normalize(incoming_name)

    best: str | None = None
    best_score = 0

    try:
        for entry in os.scandir(tv_dir):
            if not entry.is_dir():
                continue
            folder_show = re.sub(r"\s*\{tmdb-\d+\}$", "", entry.name).strip()
            norm_folder = _normalize(folder_show)
            if not norm_folder:
                continue
            # Prefix match in either direction — longer match wins
            if norm_incoming.startswith(norm_folder) or norm_folder.startswith(norm_incoming):
                score = len(norm_folder)
                if score > best_score:
                    best = entry.name
                    best_score = score
    except OSError as exc:
        logger.warning(f"Could not scan TV library at {tv_dir}: {exc}")

    return best


def _fix_permissions_tree(path: str) -> None:
    """Set nobody:users 777 on every file and directory under path (best-effort)."""
    try:
        os.chmod(path, 0o777)
        os.chown(path, _NOBODY_UID, _NOBODY_GID)
    except OSError as exc:
        logger.warning(f"chown failed on {path}: {exc}")
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(root, name)
            try:
                os.chmod(p, 0o777)
                os.chown(p, _NOBODY_UID, _NOBODY_GID)
            except OSError as exc:
                logger.warning(f"chown failed on {p}: {exc}")


def _plex_refresh() -> None:
    """Trigger a targeted Plex TV section refresh — best-effort, non-fatal."""
    try:
        from . import plex as plex_svc
        plex_svc.refresh_tv_section()
    except Exception as exc:
        logger.warning(f"Plex TV refresh failed (non-fatal): {exc}")


def run_tv_organize(job_id: int, source_path: str) -> None:
    from .job_manager import update_job

    incoming_name = os.path.basename(source_path)

    update_job(job_id, status="running", progress=5,
               message="Scanning TV library for existing series folder…")

    existing_folder = find_existing_tv_folder(incoming_name)

    if existing_folder:
        output_storage = f"{_STORAGE_ROOT}/tv/{existing_folder}"
        fmt = "Season {s}/{n} - {s00e00} - {t}"
        logger.info(
            f"TV organize: '{incoming_name}' matched to existing library folder '{existing_folder}'"
        )
        update_job(job_id, progress=10,
                   message=f"Matched to '{existing_folder}'. Running FileBot…")
    else:
        output_storage = f"{_STORAGE_ROOT}/tv"
        fmt = "{n} {tmdb-{id}}/Season {s}/{n} - {s00e00} - {t}"
        logger.info(
            f"TV organize: no existing folder for '{incoming_name}'; FileBot will create one."
        )
        update_job(job_id, progress=10,
                   message="No existing series folder — FileBot will create one. Running…")

    source_storage = _to_storage_path(source_path)

    cmd = [
        "docker", "exec", FILEBOT_CONTAINER,
        FILEBOT_BIN, "-rename",
        source_storage,
        "-r",
        "--db", "TheMovieDB::TV",
        "--output", output_storage,
        "--format", fmt,
        "--action", "move",
        "--lang", "en",
        "-non-strict",
    ]

    logger.info("FileBot cmd: %s", " ".join(cmd))
    update_job(job_id, progress=15, message="FileBot identifying and moving episodes…")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        update_job(job_id, status="error",
                   message="FileBot timed out after 5 minutes.")
        return
    except FileNotFoundError:
        update_job(job_id, status="error",
                   message=(
                       "'docker' not found. Mount /var/run/docker.sock into the app container "
                       "so it can exec into the FileBot container."
                   ))
        return

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        logger.info("FileBot stdout:\n%s", stdout)
    if stderr:
        logger.info("FileBot stderr:\n%s", stderr)

    if result.returncode != 0:
        update_job(job_id, status="error",
                   message=f"FileBot exited {result.returncode}: {(stderr or stdout)[:500]}")
        return

    moved = len(re.findall(r"\[(?:MOVE|TEST)\]", stdout))
    if moved == 0:
        update_job(job_id, status="error",
                   message=f"FileBot ran but matched 0 files. Output: {stdout[:500]}")
        return

    update_job(job_id, progress=80,
               message=f"FileBot moved {moved} episode(s). Fixing permissions…")

    # Fix permissions on the destination tree
    if existing_folder:
        perm_root = os.path.join(settings.media_dir, "tv", existing_folder)
    else:
        perm_root = os.path.join(settings.media_dir, "tv")
    _fix_permissions_tree(perm_root)

    # Remove source dir if empty
    try:
        if not any(True for _ in os.scandir(source_path)):
            os.rmdir(source_path)
            logger.info("Removed empty source dir: %s", source_path)
        else:
            logger.info(
                "Source dir not empty after organize — leaving in place: %s", source_path
            )
    except OSError as exc:
        logger.warning("Could not clean up source dir %s: %s", source_path, exc)

    _plex_refresh()

    dest_label = existing_folder or "new series folder"
    update_job(job_id, status="done", progress=100,
               message=f"Organized {moved} episode(s) → {dest_label}.")
