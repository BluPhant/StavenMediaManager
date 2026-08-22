"""
Audiobooks router — Audible/Audnexus-keyed metadata matching for incoming items.

GET    /api/audiobooks/search?q=&author=     — Audible title search → candidates
GET    /api/audiobooks/match?category=&item= — existing match for one item
GET    /api/audiobooks/matches?category=     — all matches (dict keyed by item_name)
POST   /api/audiobooks/match                 — create or update a match
DELETE /api/audiobooks/match?category=&item= — remove a match
"""
import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import AudiobookMatch
from ..services.audiobooks import lookup_by_asin, search_audible
from ..services.movies import clean_for_search

_ASIN_RE = re.compile(r'(?<![A-Za-z0-9])(B[0-9A-Z]{9})(?![A-Za-z0-9])')

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audiobooks", tags=["audiobooks"])


class MatchRequest(BaseModel):
    category: str
    item_name: str
    asin: str | None = None
    title: str
    author: str | None = None
    narrator: str | None = None
    year: int | None = None
    cover_url: str | None = None
    series_title: str | None = None
    series_sequence: str | None = None
    duration_minutes: int | None = None
    formatted_name: str


@router.get("/search")
def search_audiobooks(
    q: str = Query(""),
    author: str = Query(""),
):
    """Audible catalog search — returns candidate list for the confirm step."""
    if not q and not author:
        raise HTTPException(status_code=400, detail="Provide q (title) and/or author.")
    try:
        return search_audible(q, author)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/lookup")
def lookup_audiobook(asin: str = Query(..., min_length=1)):
    """Direct ASIN lookup — Audnexus first, Audible catalog fallback."""
    result = lookup_by_asin(asin.strip())
    if not result:
        raise HTTPException(status_code=404, detail=f"No book found for ASIN {asin}")
    return result


@router.get("/match")
def get_match(
    category: str = Query(...),
    item: str = Query(...),
    db: Session = Depends(get_db),
):
    """Return existing match (if any) for a single incoming item, plus a suggested query and ASIN."""
    match = (
        db.query(AudiobookMatch)
        .filter(AudiobookMatch.category == category, AudiobookMatch.item_name == item)
        .first()
    )
    # Use only the leaf component (last path segment) so bundle prefixes like
    # "Full Cast/" don't pollute the search query.
    leaf = item.split("/")[-1]
    # Strip leading track numbers common in audiobook folder names: "01 ", "1. ", "03 - "
    leaf = re.sub(r"^\d+[\s.\-]+", "", leaf)
    query, year = clean_for_search(leaf)
    suggested_asin = _detect_asin(category, item)
    return {
        "match":           _serialize(match) if match else None,
        "suggested_query": query,
        "suggested_year":  year,
        "suggested_asin":  suggested_asin,
    }


@router.get("/matches")
def get_matches(
    category: str = Query(...),
    db: Session = Depends(get_db),
):
    """All matches for a category as a dict keyed by item_name."""
    rows = (
        db.query(AudiobookMatch)
        .filter(AudiobookMatch.category == category)
        .all()
    )
    return {r.item_name: _serialize(r) for r in rows}


@router.post("/match", status_code=201)
def save_match(req: MatchRequest, db: Session = Depends(get_db)):
    """Create or update a match for an incoming item."""
    existing = (
        db.query(AudiobookMatch)
        .filter(AudiobookMatch.category == req.category,
                AudiobookMatch.item_name == req.item_name)
        .first()
    )
    data = req.dict()
    if existing:
        for field, val in data.items():
            setattr(existing, field, val)
        db.commit()
        db.refresh(existing)
        return _serialize(existing)

    record = AudiobookMatch(**data)
    db.add(record)
    db.commit()
    db.refresh(record)
    return _serialize(record)


@router.delete("/match")
def delete_match(
    category: str = Query(...),
    item: str = Query(...),
    db: Session = Depends(get_db),
):
    """Remove a match."""
    match = (
        db.query(AudiobookMatch)
        .filter(AudiobookMatch.category == category, AudiobookMatch.item_name == item)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match not found.")
    db.delete(match)
    db.commit()
    return {"deleted": True}


@router.get("/presence-check")
def presence_check(
    title: str = Query(..., min_length=1),
    author: str = Query(""),
):
    """
    Check whether a book exists in the local library and/or on the seedbox.
    Returns {library: {found, match}, seedbox: {found, match, error}}.
    """
    needle = _norm(title)

    # ── Library scan ──────────────────────────────────────────────────────────
    library_dir = os.path.join(settings.media_dir, "audiobooks")
    lib_found, lib_match = False, None
    try:
        for entry in os.scandir(library_dir):
            if _norm(entry.name).find(needle) != -1:
                lib_found, lib_match = True, entry.name
                break
    except OSError:
        pass

    # ── Seedbox scan ──────────────────────────────────────────────────────────
    sbx_found, sbx_match, sbx_error = False, None, None
    try:
        from ..services.sources import get_active_source
        source = get_active_source()
        if source:
            torrents = source.list_all_brief()
            for info in torrents.values():
                name = info.get("name", "")
                if _norm(name).find(needle) != -1:
                    sbx_found, sbx_match = True, name
                    break
        else:
            sbx_error = "Not configured"
    except Exception as exc:
        sbx_error = str(exc)

    return {
        "library": {"found": lib_found, "match": lib_match},
        "seedbox": {"found": sbx_found, "match": sbx_match, "error": sbx_error},
    }


def _norm(s: str) -> str:
    """Lowercase, strip punctuation and extra spaces for fuzzy matching."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _detect_asin(category: str, item_name: str) -> str | None:
    """
    Find an Audible ASIN for this item, checking in order:
      1. The folder/item name itself
      2. File names inside the folder
      3. AUDIBLE_ASIN tag embedded in any .m4b / .aax / .aaxc file's metadata
    """
    # 1. Folder name
    m = _ASIN_RE.search(item_name)
    if m:
        return m.group(1)

    item_path = os.path.join(settings.incoming_dir, category, item_name)

    # 2. Filenames
    try:
        for fname in os.listdir(item_path):
            m = _ASIN_RE.search(fname)
            if m:
                return m.group(1)
    except OSError:
        pass

    # 3. Embedded metadata (AUDIBLE_ASIN tag written by Audible download tools)
    return _asin_from_metadata(item_path)


def _asin_from_metadata(folder: str) -> str | None:
    """Read AUDIBLE_ASIN from the first audio file's ffprobe tags."""
    import subprocess
    _AUDIO_EXTS = {".m4b", ".aax", ".aaxc", ".mp3", ".m4a"}
    logger.debug("_asin_from_metadata scanning: %r", folder)
    try:
        entries = sorted(os.listdir(folder))
    except OSError as exc:
        logger.warning("_asin_from_metadata: cannot list %r: %s", folder, exc)
        return None
    for fname in entries:
        if os.path.splitext(fname)[1].lower() not in _AUDIO_EXTS:
            continue
        fpath = os.path.join(folder, fname)
        logger.debug("_asin_from_metadata: probing %r", fpath)
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format_tags",
                 "-of", "default", fpath],
                capture_output=True, text=True, timeout=10,
            )
        except Exception as exc:
            logger.warning("_asin_from_metadata: ffprobe failed on %r: %s", fpath, exc)
            continue
        logger.debug("_asin_from_metadata: stdout=%r stderr=%r", result.stdout[:300], result.stderr[:200])
        for line in result.stdout.splitlines():
            if line.upper().startswith("TAG:AUDIBLE_ASIN="):
                asin = line.split("=", 1)[1].strip()
                if asin:
                    logger.info("_asin_from_metadata: found ASIN %s in %r", asin, fpath)
                    return asin
            # Also catch bare ASIN patterns in any tag value
            m = _ASIN_RE.search(line)
            if m:
                logger.info("_asin_from_metadata: found ASIN %s via regex in %r", m.group(1), fpath)
                return m.group(1)
    return None


def _serialize(m: AudiobookMatch) -> dict:
    return {
        "id":               m.id,
        "category":         m.category,
        "item_name":        m.item_name,
        "asin":             m.asin,
        "title":            m.title,
        "author":           m.author,
        "narrator":         m.narrator,
        "year":             m.year,
        "cover_url":        m.cover_url,
        "series_title":     m.series_title,
        "series_sequence":  m.series_sequence,
        "duration_minutes": m.duration_minutes,
        "formatted_name":   m.formatted_name,
    }
