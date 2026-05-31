"""
Library search — check whether a title already exists in the local media library.

GET /api/library/search?q=<title>
  Walks settings.media_dir for video files whose name fuzzy-matches the query.
  Returns [{filename, rel_path, category}], capped at 10 results.
"""
import os
import re

from fastapi import APIRouter

from ..config import settings

router = APIRouter(prefix="/library", tags=["library"])

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv"}
_MAX_FILES   = 2000   # safety cap on files walked


def _norm(s: str) -> str:
    """Strip release tags, punctuation, and collapse whitespace for loose matching."""
    s = s.lower()
    s = re.sub(
        r"\b(2160p|1080p|720p|480p|4k|uhd|hdr\w*|web[-.]?dl|webrip|bluray|bdrip|"
        r"hevc|h\.?26[45]|avc|x26[45]|dts|aac|dd[p+]?\d*|atmos|remux|repack|"
        r"proper|extended|directors?\.?cut|\d{4})\b",
        " ",
        s,
    )
    s = re.sub(r"[._\-\[\]()]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@router.get("/search")
def library_search(q: str = "") -> list[dict]:
    """
    Fuzzy-search the local media library for an existing title.

    q — title to look for (e.g. "Sound of Music" or "Iron Lung 2026")

    Response: [{filename, rel_path, category}]
    """
    if not q:
        return []

    media_dir = settings.media_dir
    if not os.path.isdir(media_dir):
        return []

    # Build query words — keep words >= 2 chars after normalisation
    query_words = [w for w in _norm(q).split() if len(w) >= 2]
    if not query_words:
        return []

    matches: list[dict] = []
    files_walked = 0

    for root, dirs, files in os.walk(media_dir):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        for fname in files:
            files_walked += 1
            if files_walked > _MAX_FILES:
                break

            ext = os.path.splitext(fname)[1].lower()
            if ext not in _VIDEO_EXTS:
                continue

            # Match against the stem (filename without extension)
            stem = os.path.splitext(fname)[0]
            norm = _norm(stem)
            if not all(w in norm for w in query_words):
                continue

            rel  = os.path.relpath(os.path.join(root, fname), media_dir)
            rel  = rel.replace("\\", "/")
            # Category = first component of the relative path
            parts    = rel.split("/")
            category = parts[0] if len(parts) > 1 else ""

            matches.append({"filename": fname, "rel_path": rel, "category": category})

        if files_walked > _MAX_FILES:
            break

    return matches[:10]
