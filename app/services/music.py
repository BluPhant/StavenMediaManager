"""
Music import — converts a folder of FLACs to tagged MP3 V0, organizes the
result into the library layout, and triggers a Plex refresh.

This is a Python port of the user's Convert-FlacToMp3.ps1 (run manually on
Windows against Z:\\temp\\<release>), adapted to run inside the container as a
background Job against /media/temp/Incoming/music/<item> and to finish by
relocating the finished album into the library:

    /media/music/<FirstLetter>/<Artist>/<Artist> - <Album> (<Year>)/

e.g. /media/music/Y/Young The Giant/Young The Giant - My Album (2026)/

Pipeline (mirrors the PS1 script's five passes):
  1. Tag validation — title/artist/album/tracknumber required on every FLAC;
     hard stop (no files touched) if anything is missing.
  2. Conversion     — FLAC -> MP3 V0 (-q:a 0, ~245kbps VBR), cover art embedded
     (folder JPG used as fallback if not already embedded), track number
     zero-padded with total (e.g. 05/09), verified via ffprobe, FLAC deleted.
  3. Track rename   — "01 - Title.mp3"
  4. Folder + dest  — "Artist - Album (Year)", relocated under
     /media/music/<Letter>/<Artist>/...
  5. Cleanup        — .nfo/.sfv/.m3u(8) removed; anything else flagged in the
     job message for manual review.

If FLACs are absent but MP3s already exist (already-converted release),
passes 1-2 are skipped and the existing MP3s are renamed/organized as-is —
same behaviour as the PS1 script.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import urllib.request

from ..config import settings
from .job_manager import update_job, is_cancelled
from .mover import dest_subdir

logger = logging.getLogger(__name__)

_COVER_EXTS   = (".jpg", ".jpeg", ".png")
_JUNK_EXTS    = {".nfo", ".sfv", ".m3u", ".m3u8"}
_ALLOWED_EXTS = {".mp3", ".jpg", ".jpeg", ".png"}

_ILLEGAL_RE     = re.compile(r'[\\/:*?"<>|]')
_TRACK_SLASH_RE = re.compile(r'^(\d+)/(\d+)$')
_TRACK_NUM_RE   = re.compile(r'^(\d+)')
_YEAR_RE        = re.compile(r'(\d{4})')


# ── ffprobe / tag helpers (port of Get-ProbeJson / Get-NormalizedTags / Get-TagValue) ──

def _probe(path: str) -> dict | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(f"ffprobe failed on {path}: {exc}")
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def _tags(probe: dict | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if probe and isinstance(probe.get("format"), dict):
        for k, v in (probe["format"].get("tags") or {}).items():
            if v is not None:
                result[k.lower()] = str(v)
    return result


def _tag(tags: dict, *keys: str) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v and v.strip():
            return v.strip()
    return None


def _format_track(raw: str | None, total: int) -> str | None:
    if not raw:
        return None
    m = _TRACK_SLASH_RE.match(raw)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}"
    m = _TRACK_NUM_RE.match(raw)
    if m:
        n = int(m.group(1))
        return f"{n:02d}/{total:02d}" if total > 0 else f"{n:02d}"
    return raw


def _has_video_stream(probe: dict | None) -> bool:
    if not probe:
        return False
    return any(s.get("codec_type") == "video" for s in probe.get("streams", []))


def _audio_ok(probe: dict | None) -> bool:
    if not probe:
        return False
    return any(s.get("codec_type") == "audio" for s in probe.get("streams", []))


def _sanitize(s: str) -> str:
    return _ILLEGAL_RE.sub("_", s).strip(" .")


# ── main entry point ──────────────────────────────────────────────────────────

def run_music_import(job_id: int, source_path: str) -> None:
    update_job(job_id, status="running", progress=2, message="Scanning folder…")

    folder = source_path
    try:
        entries = sorted(os.listdir(folder))
    except OSError as exc:
        update_job(job_id, status="error", message=f"Cannot read folder: {exc}")
        return

    flacs = [f for f in entries if f.lower().endswith(".flac")]
    mp3_exists = any(f.lower().endswith(".mp3") for f in entries)

    if not flacs and not mp3_exists:
        update_job(job_id, status="error", message="No FLAC or MP3 files found in folder.")
        return

    cover_file = next(
        (f for f in entries if os.path.splitext(f)[1].lower() in _COVER_EXTS), None
    )

    # ── Pass 1/5 — Tag validation (FLACs only; hard stop on any failure) ──────
    if flacs:
        update_job(job_id, progress=5,
                   message=f"Pass 1/5 — Validating tags on {len(flacs)} FLAC(s)…")
        problems = []
        for name in flacs:
            probe = _probe(os.path.join(folder, name))
            if not probe:
                problems.append(f"{name} — unreadable by ffprobe")
                continue
            tags = _tags(probe)
            missing = [
                label for label, keys in (
                    ("title", ("title",)),
                    ("artist", ("artist", "albumartist")),
                    ("album", ("album",)),
                    ("tracknumber", ("tracknumber", "track")),
                ) if not _tag(tags, *keys)
            ]
            if missing:
                problems.append(f"{name} — missing: {', '.join(missing)}")

        if problems:
            update_job(
                job_id, status="error",
                message=("Tag validation FAILED — fix tags and re-run. No files were modified. "
                         + " | ".join(problems[:20])),
            )
            return
        update_job(job_id, progress=10, message="Pass 1/5 — Tags OK.")

    # ── Pass 2/5 — Convert FLAC -> MP3 V0, verify, delete FLAC ────────────────
    if flacs:
        total = len(flacs)
        for idx, name in enumerate(flacs, 1):
            if is_cancelled(job_id):
                update_job(job_id, status="cancelled", message="Cancelled during conversion.")
                return

            flac_path = os.path.join(folder, name)
            mp3_path  = os.path.join(folder, os.path.splitext(name)[0] + ".mp3")

            probe = _probe(flac_path)
            tags  = _tags(probe)
            track_padded   = _format_track(_tag(tags, "tracknumber", "track"), total)
            embedded_cover = _has_video_stream(probe)

            cmd = ["ffmpeg", "-y", "-i", flac_path]
            if embedded_cover:
                cmd += ["-map", "0:a", "-map", "0:v", "-disposition:v:0", "attached_pic"]
            elif cover_file:
                cmd += ["-i", os.path.join(folder, cover_file),
                        "-map", "0:a", "-map", "1:v", "-disposition:v:0", "attached_pic"]
            else:
                cmd += ["-map", "0:a"]

            cmd += ["-q:a", "0", "-id3v2_version", "3"]
            if track_padded:
                cmd += ["-metadata", f"track={track_padded}"]
            cmd.append(mp3_path)

            update_job(job_id, progress=10 + int(idx / total * 45),
                       message=f"Pass 2/5 — Converting {idx}/{total}: {name}")

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            except (OSError, subprocess.TimeoutExpired) as exc:
                update_job(job_id, status="error", message=f"ffmpeg failed on {name}: {exc}")
                return

            if proc.returncode != 0 or not os.path.exists(mp3_path):
                update_job(job_id, status="error",
                           message=f"ffmpeg conversion failed on {name}: {proc.stderr[-400:]}")
                return

            if not _audio_ok(_probe(mp3_path)):
                try:
                    os.remove(mp3_path)
                except OSError:
                    pass
                update_job(job_id, status="error",
                           message=f"MP3 verification failed for {name} — stopped, FLAC kept.")
                return

            os.remove(flac_path)

        update_job(job_id, progress=55, message=f"Pass 2/5 — Converted {total} track(s).")

    # ── Pass 3/5 — Rename tracks "01 - Title.mp3" ─────────────────────────────
    update_job(job_id, progress=58, message="Pass 3/5 — Renaming tracks…")
    mp3s = sorted(f for f in os.listdir(folder) if f.lower().endswith(".mp3"))
    for name in mp3s:
        path  = os.path.join(folder, name)
        tags  = _tags(_probe(path))
        title = _tag(tags, "title")
        if not title:
            continue

        track_num = ""
        track_raw = _tag(tags, "tracknumber", "track")
        if track_raw:
            m = _TRACK_NUM_RE.match(track_raw)
            if m:
                track_num = f"{int(m.group(1)):02d}"

        safe_title = _sanitize(title)
        new_name = f"{track_num} - {safe_title}.mp3" if track_num else f"{safe_title}.mp3"
        if name == new_name:
            continue
        new_path = os.path.join(folder, new_name)
        if os.path.exists(new_path):
            new_path = os.path.join(folder, f"{os.path.splitext(new_name)[0]} ({name}")
        os.rename(path, new_path)

    # ── Pass 4/5 — Derive Artist/Album/Year, compute destination ──────────────
    update_job(job_id, progress=65, message="Pass 4/5 — Reading album info…")
    mp3s = sorted(f for f in os.listdir(folder) if f.lower().endswith(".mp3"))
    if not mp3s:
        update_job(job_id, status="error",
                   message="No MP3 files present after conversion — nothing to import.")
        return

    first_tags = _tags(_probe(os.path.join(folder, mp3s[0])))
    artist = _tag(first_tags, "albumartist", "artist") or "Unknown Artist"
    album  = _tag(first_tags, "album") or "Unknown Album"
    year   = _tag(first_tags, "date", "year")
    if year:
        ym = _YEAR_RE.search(year)
        year = ym.group(1) if ym else None

    artist_safe = _sanitize(artist)
    album_safe  = _sanitize(album)
    album_folder = f"{artist_safe} - {album_safe} ({year})" if year else f"{artist_safe} - {album_safe}"
    letter = artist_safe[0].upper() if artist_safe and artist_safe[0].isalpha() else "#"

    dest_dir = os.path.join(settings.media_dir, dest_subdir("music"), letter, artist_safe, album_folder)

    # ── Pass 5/5 — Cleanup: drop junk, flag anything unexpected ───────────────
    update_job(job_id, progress=72, message="Pass 5/5 — Cleaning up…")
    removed, flagged = [], []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in _JUNK_EXTS:
            try:
                os.remove(path)
                removed.append(name)
            except OSError:
                pass
        elif ext not in _ALLOWED_EXTS:
            flagged.append(name)

    # ── Move into library layout ──────────────────────────────────────────────
    update_job(job_id, progress=80, message=f"Moving to {dest_dir}…")
    try:
        os.makedirs(dest_dir, exist_ok=True)
        for name in os.listdir(folder):
            shutil.move(os.path.join(folder, name), os.path.join(dest_dir, name))
        try:
            os.rmdir(folder)
        except OSError:
            pass
    except Exception as exc:
        update_job(job_id, status="error", message=f"Move to {dest_dir} failed: {exc}")
        return

    threading.Thread(target=_refresh_music_library, daemon=True).start()

    suffix = ""
    if removed:
        suffix += f" Removed: {', '.join(removed)}."
    if flagged:
        suffix += f" ⚠ Flagged for manual review: {', '.join(flagged)}."

    update_job(
        job_id, status="done", progress=100, dest_path=dest_dir,
        message=f"Imported {len(mp3s)} track(s) → {dest_dir}.{suffix} Plex refresh queued.",
    )


def _refresh_music_library() -> None:
    """Best-effort full Plex library refresh (fire-and-forget, never blocks the job)."""
    if not (settings.plex_url and settings.plex_token):
        return
    try:
        base = settings.plex_url.rstrip("/")
        url = f"{base}/library/sections/all/refresh?X-Plex-Token={settings.plex_token}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10):  # noqa: S310
            pass
        logger.info("Plex refresh triggered after music import.")
    except Exception as exc:
        logger.warning(f"Plex refresh failed (non-fatal): {exc}")
