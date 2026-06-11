"""
Music router — Discogs metadata lookup, match persistence, tag pre-read.

Endpoints:
  GET  /music/configured        — is Discogs token set?
  GET  /music/tags              — read artist/album/year from first audio file
  GET  /music/search            — search Discogs for artist+album
  GET  /music/release/{id}      — full release details (tracklist, cover)
  GET  /music/match             — get existing MusicMatch for an item
  POST /music/match             — save (upsert) a confirmed MusicMatch
  DELETE /music/match           — remove a MusicMatch
"""
import json
import logging
import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import MusicMatch
from ..services.discogs import DiscogsClient

logger = logging.getLogger(__name__)
router = APIRouter(tags=["music"])

_discogs = DiscogsClient()


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── helpers ───────────────────────────────────────────────────────────────────

def _item_folder(category: str, item: str) -> str:
    return os.path.join(settings.incoming_dir, category, item)


def _first_audio(folder: str) -> str | None:
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return None
    for name in entries:
        if name.lower().endswith((".flac", ".mp3")):
            return os.path.join(folder, name)
    return None


def _probe_tags(path: str) -> dict[str, str]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if out.returncode != 0:
        return {}
    try:
        raw = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}
    result: dict[str, str] = {}
    for k, v in (raw.get("format", {}).get("tags") or {}).items():
        if v:
            result[k.lower()] = str(v).strip()
    return result


def _tag(tags: dict, *keys: str) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v:
            return v
    return None


def _match_to_dict(m: MusicMatch) -> dict:
    tracks = []
    if m.tracks_json:
        try:
            tracks = json.loads(m.tracks_json)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id":         m.id,
        "discogs_id": m.discogs_id,
        "artist":     m.artist,
        "album":      m.album,
        "year":       m.year,
        "label":      m.label,
        "cover_url":  m.cover_url,
        "genres":     m.genres,
        "country":    m.country,
        "tracks":     tracks,
    }


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/music/configured")
def music_configured():
    return {"configured": _discogs.is_configured()}


@router.get("/music/tags")
def music_tags(category: str, item: str):
    """Read the first audio file's tags and return artist/album/year for pre-filling search."""
    folder = _item_folder(category, item)
    audio  = _first_audio(folder)
    if not audio:
        return {"artist": None, "album": None, "year": None}

    tags   = _probe_tags(audio)
    artist = _tag(tags, "albumartist", "artist")
    album  = _tag(tags, "album")
    year_s = _tag(tags, "date", "year")
    year   = None
    if year_s:
        import re
        m = re.search(r"(\d{4})", year_s)
        year = m.group(1) if m else None

    return {"artist": artist, "album": album, "year": year}


@router.get("/music/search")
def music_search(artist: str = "", album: str = "", limit: int = 10):
    if not _discogs.is_configured():
        raise HTTPException(status_code=503, detail="DISCOGS_TOKEN not configured.")
    if not artist and not album:
        raise HTTPException(status_code=400, detail="Provide at least artist or album.")
    try:
        results = _discogs.search(artist=artist, album=album, limit=limit)
    except Exception as exc:
        logger.error(f"Discogs search error: {exc}")
        raise HTTPException(status_code=502, detail=f"Discogs API error: {exc}") from exc

    return [
        {
            "id":           r.id,
            "artist":       r.artist,
            "title":        r.title,
            "year":         r.year,
            "label":        r.label,
            "format":       r.format,
            "thumb":        r.thumb,
            "cover_image":  r.cover_image,
            "country":      r.country,
            "genres":       r.genres,
            "styles":       r.styles,
        }
        for r in results
    ]


@router.get("/music/release/{release_id}")
def music_release(release_id: int):
    if not _discogs.is_configured():
        raise HTTPException(status_code=503, detail="DISCOGS_TOKEN not configured.")
    try:
        rel = _discogs.get_release(release_id)
    except Exception as exc:
        logger.error(f"Discogs release {release_id} error: {exc}")
        raise HTTPException(status_code=502, detail=f"Discogs API error: {exc}") from exc

    return {
        "id":        rel.id,
        "artist":    rel.artist,
        "title":     rel.title,
        "year":      rel.year,
        "label":     rel.label,
        "catno":     rel.catno,
        "cover_url": rel.cover_url,
        "country":   rel.country,
        "genres":    rel.genres,
        "styles":    rel.styles,
        "tracks": [
            {"position": t.position, "title": t.title, "duration": t.duration}
            for t in rel.tracks
        ],
    }


@router.get("/music/matches")
def get_matches(category: str, db: Session = Depends(_db)):
    """Return all matches for a category as {item_name: {artist, album, year}} for the list view."""
    rows = db.query(MusicMatch).filter_by(category=category).all()
    return {
        r.item_name: {"artist": r.artist, "album": r.album, "year": r.year}
        for r in rows
    }


@router.get("/music/match")
def get_match(category: str, item: str, db: Session = Depends(_db)):
    m = db.query(MusicMatch).filter_by(category=category, item_name=item).first()
    return {"match": _match_to_dict(m) if m else None}


class MatchBody(BaseModel):
    category:   str
    item_name:  str
    discogs_id: int
    artist:     str
    album:      str
    year:       int | None = None
    label:      str | None = None
    cover_url:  str | None = None
    genres:     str | None = None
    country:    str | None = None
    tracks_json: str | None = None


@router.post("/music/match")
def save_match(body: MatchBody, db: Session = Depends(_db)):
    m = db.query(MusicMatch).filter_by(
        category=body.category, item_name=body.item_name
    ).first()
    if m:
        m.discogs_id  = body.discogs_id
        m.artist      = body.artist
        m.album       = body.album
        m.year        = body.year
        m.label       = body.label
        m.cover_url   = body.cover_url
        m.genres      = body.genres
        m.country     = body.country
        m.tracks_json = body.tracks_json
    else:
        m = MusicMatch(
            category    = body.category,
            item_name   = body.item_name,
            discogs_id  = body.discogs_id,
            artist      = body.artist,
            album       = body.album,
            year        = body.year,
            label       = body.label,
            cover_url   = body.cover_url,
            genres      = body.genres,
            country     = body.country,
            tracks_json = body.tracks_json,
        )
        db.add(m)
    db.commit()
    db.refresh(m)
    return _match_to_dict(m)


@router.delete("/music/match")
def delete_match(category: str, item: str, db: Session = Depends(_db)):
    m = db.query(MusicMatch).filter_by(category=category, item_name=item).first()
    if m:
        db.delete(m)
        db.commit()
    return {"ok": True}
