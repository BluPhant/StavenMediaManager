"""
Switch router — game lookup, title/content library management.

Endpoints:
  GET  /switch/detect               — auto-detect game ID + nswdb match for an incoming folder
  GET  /switch/search               — search by title (IGDB + nswdb)
  GET  /switch/lookup/{game_id}     — fetch game info from GameTDB by 5-char ID
  GET  /switch/title/{game_id}      — get SwitchTitle from local DB
  GET  /switch/titles               — list all titles in library
  GET  /switch/content              — get SwitchContent record for item_name
  POST /switch/match                — confirm match: upsert SwitchTitle + SwitchContent
  POST /switch/move                 — submit move job
  DELETE /switch/content            — remove a SwitchContent record
"""
import logging
import os
import re
import urllib.request

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import SwitchContent, SwitchTitle
from ..services import gametdb as tdb
from ..services import igdb as igdb_svc
from ..services import nswdb as nswdb_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["switch"])

_COVER_DIR = os.path.join(settings.config_dir, "switch_covers")
_REV_RE = re.compile(r'\s*\[rev\s+[\d.]+\]', re.IGNORECASE)


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _title_to_dict(t: SwitchTitle, contents: list[SwitchContent] | None = None) -> dict:
    return {
        "id":          t.id,
        "game_id":     t.game_id,
        "igdb_id":     t.igdb_id,
        "nintendo_id": t.nintendo_id,
        "title":       t.title,
        "developer":   t.developer,
        "publisher":   t.publisher,
        "cover_url":   t.cover_url,
        "cover_local": t.cover_local,
        "library_path": t.library_path,
        "created_at":  t.created_at,
        "updated_at":  t.updated_at,
        "contents":    [_content_to_dict(c) for c in (contents or [])],
    }


def _content_to_dict(c: SwitchContent) -> dict:
    return {
        "id":           c.id,
        "title_id":     c.title_id,
        "item_name":    c.item_name,
        "content_type": c.content_type,
        "version":      c.version,
        "dlc_name":     c.dlc_name,
        "filename":     c.filename,
        "file_size":    c.file_size,
        "library_path": c.library_path,
        "created_at":   c.created_at,
    }


# ── Detection & search ────────────────────────────────────────────────────────

@router.get("/switch/detect")
def detect_id(item_name: str, category: str = "switch-games"):
    """
    Auto-detect game info for an incoming item:
    1. Tries to extract a GameTDB ID from scene file names in the folder.
    2. Tries to match the folder name against the nswdb release database.
    3. Parses content type (base/update/DLC) from the folder name.
    """
    folder = os.path.join(settings.incoming_dir, category, item_name)
    if not os.path.isdir(folder):
        raise HTTPException(status_code=404, detail="Item folder not found")

    # Scan for game files and try to extract a GameTDB ID
    detected_gametdb_id = None
    try:
        for fname in os.listdir(folder):
            if os.path.splitext(fname)[1].lower() in (".xci", ".nsp", ".nsz"):
                candidate = tdb.extract_id_from_filename(fname)
                if candidate:
                    detected_gametdb_id = candidate
                    break
    except OSError:
        pass

    if not detected_gametdb_id:
        candidate = tdb.extract_id_from_filename(item_name)
        if candidate:
            detected_gametdb_id = candidate

    # nswdb release name match
    nswdb_match = nswdb_svc.match_release(item_name)
    nswdb_title = nswdb_publisher = nintendo_id = None
    if nswdb_match:
        nswdb_title = _REV_RE.sub("", nswdb_match["name"]).strip()
        nswdb_publisher = nswdb_match.get("publisher") or None
        nintendo_id = nswdb_match.get("titleid") or None

    content_type, version, dlc_name = tdb.parse_content_type(item_name)

    return {
        "game_id":         detected_gametdb_id,
        "guessed_title":   tdb.clean_title_from_folder(item_name),
        "nswdb_title":     nswdb_title,
        "nswdb_publisher": nswdb_publisher,
        "nintendo_id":     nintendo_id,
        "content_type":    content_type,
        "version":         version,
        "dlc_name":        dlc_name,
    }


@router.get("/switch/search")
def search_titles(q: str):
    """
    Search for Switch games by title.
    IGDB results (with cover art) come first; nswdb fills the gaps.
    IGDB requires IGDB_CLIENT_ID + IGDB_CLIENT_SECRET env vars.
    """
    igdb_results: list[dict] = []
    if settings.igdb_client_id and settings.igdb_client_secret:
        try:
            igdb_results = igdb_svc.search_games(
                q, settings.igdb_client_id, settings.igdb_client_secret, limit=8
            )
        except Exception as exc:
            logger.warning(f"IGDB search failed: {exc}")

    nswdb_results = nswdb_svc.search_by_name(q, limit=8)
    return {"igdb": igdb_results, "nswdb": nswdb_results}


# ── GameTDB direct lookup ─────────────────────────────────────────────────────

@router.get("/switch/lookup/{game_id}")
def lookup_game(game_id: str):
    """Fetch game metadata from GameTDB by its 5-char ID (live scrape)."""
    game = tdb.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found on GameTDB")
    return {
        "game_id":   game.game_id,
        "title":     game.title,
        "developer": game.developer,
        "publisher": game.publisher,
        "cover_url": game.cover_url,
    }


# ── Local library ─────────────────────────────────────────────────────────────

@router.get("/switch/titles")
def list_titles(db: Session = Depends(_db)):
    titles = db.query(SwitchTitle).order_by(SwitchTitle.title).all()
    result = []
    for t in titles:
        contents = db.query(SwitchContent).filter(SwitchContent.title_id == t.id).all()
        result.append(_title_to_dict(t, contents))
    return result


@router.get("/switch/title/{game_id}")
def get_title(game_id: str, db: Session = Depends(_db)):
    t = db.query(SwitchTitle).filter(SwitchTitle.game_id == game_id.upper()).first()
    if not t:
        raise HTTPException(status_code=404, detail="Title not in local library")
    contents = db.query(SwitchContent).filter(SwitchContent.title_id == t.id).all()
    return _title_to_dict(t, contents)


@router.get("/switch/content")
def get_content_for_item(item_name: str, db: Session = Depends(_db)):
    c = db.query(SwitchContent).filter(SwitchContent.item_name == item_name).first()
    if not c:
        raise HTTPException(status_code=404, detail="No match for this item")
    t = db.query(SwitchTitle).filter(SwitchTitle.id == c.title_id).first()
    return {**_content_to_dict(c), "title": _title_to_dict(t) if t else None}


# ── Match & move ──────────────────────────────────────────────────────────────

class MatchRequest(BaseModel):
    category:     str
    item_name:    str
    game_id:      str | None = None   # GameTDB 5-char ID
    igdb_id:      int | None = None   # IGDB numeric ID
    nintendo_id:  str | None = None   # Nintendo 64-bit Title ID (from nswdb)
    content_type: str = "base"        # base | update | dlc
    version:      str | None = None
    dlc_name:     str | None = None
    # Pre-filled metadata (avoids extra API round-trip when coming from search results)
    title:        str | None = None
    publisher:    str | None = None
    developer:    str | None = None
    cover_url:    str | None = None


@router.post("/switch/match", status_code=201)
def save_match(req: MatchRequest, db: Session = Depends(_db)):
    """
    Confirm a match between an incoming item and a game.
    Accepts a GameTDB ID, IGDB ID, or pre-filled title metadata.
    Upserts SwitchTitle + SwitchContent, caches cover art.
    """
    game_id     = (req.game_id or "").strip().upper() or None
    igdb_id     = req.igdb_id
    nintendo_id = req.nintendo_id or None

    if not game_id and not igdb_id and not req.title:
        raise HTTPException(status_code=400, detail="Provide game_id, igdb_id, or title")

    # Find existing title record by any matching identifier
    title_rec = None
    if game_id:
        title_rec = db.query(SwitchTitle).filter(SwitchTitle.game_id == game_id).first()
    if not title_rec and igdb_id:
        title_rec = db.query(SwitchTitle).filter(SwitchTitle.igdb_id == igdb_id).first()
    # Fallback: match by title so updates/DLC link to an existing base-game record
    if not title_rec and req.title:
        from sqlalchemy import func as _func
        title_rec = db.query(SwitchTitle).filter(
            _func.lower(SwitchTitle.title) == req.title.strip().lower()
        ).first()

    if not title_rec:
        title_str  = req.title
        publisher  = req.publisher
        developer  = req.developer
        cover_url  = req.cover_url
        cover_local = None

        if game_id and not title_str:
            # No pre-filled title — fetch from GameTDB
            game = tdb.get_game(game_id)
            if not game:
                raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found on GameTDB")
            title_str = game.title
            publisher = game.publisher or None
            developer = game.developer or None
            cover_url = game.cover_url or None

        if not title_str:
            raise HTTPException(status_code=400, detail="Could not determine game title")

        if cover_url:
            key = game_id or (f"igdb{igdb_id}" if igdb_id else "cover")
            cover_local = _cache_cover(key, cover_url)

        title_rec = SwitchTitle(
            game_id     = game_id,
            igdb_id     = igdb_id,
            nintendo_id = nintendo_id,
            title       = title_str,
            developer   = developer,
            publisher   = publisher,
            cover_url   = cover_url,
            cover_local = cover_local,
        )
        db.add(title_rec)
        db.flush()
    else:
        # Patch any newly known identifiers onto the existing record
        if nintendo_id and not title_rec.nintendo_id:
            title_rec.nintendo_id = nintendo_id
        if igdb_id and not title_rec.igdb_id:
            title_rec.igdb_id = igdb_id
        if game_id and not title_rec.game_id:
            title_rec.game_id = game_id

    # Find primary game file in the incoming folder
    folder = os.path.join(settings.incoming_dir, req.category, req.item_name)
    filename, file_size = _find_game_file(folder)

    # Upsert SwitchContent
    content_rec = db.query(SwitchContent).filter(
        SwitchContent.item_name == req.item_name
    ).first()
    if content_rec:
        content_rec.title_id     = title_rec.id
        content_rec.content_type = req.content_type
        content_rec.version      = req.version
        content_rec.dlc_name     = req.dlc_name
        content_rec.filename     = filename
        content_rec.file_size    = file_size
    else:
        content_rec = SwitchContent(
            title_id     = title_rec.id,
            item_name    = req.item_name,
            content_type = req.content_type,
            version      = req.version,
            dlc_name     = req.dlc_name,
            filename     = filename,
            file_size    = file_size,
        )
        db.add(content_rec)

    db.commit()
    db.refresh(title_rec)
    db.refresh(content_rec)

    contents = db.query(SwitchContent).filter(SwitchContent.title_id == title_rec.id).all()
    return _title_to_dict(title_rec, contents)


class MoveRequest(BaseModel):
    category:  str
    item_name: str


@router.post("/switch/move", status_code=201)
def move_to_library(req: MoveRequest, db: Session = Depends(_db)):
    """Submit a move job for a confirmed Switch item."""
    from ..database import SessionLocal as _SL
    from ..models import Job
    from ..services import job_manager

    content = db.query(SwitchContent).filter(
        SwitchContent.item_name == req.item_name
    ).first()
    if not content:
        raise HTTPException(status_code=400, detail="No match found — confirm a match first")

    title = db.query(SwitchTitle).filter(SwitchTitle.id == content.title_id).first()
    if not title:
        raise HTTPException(status_code=400, detail="SwitchTitle record missing")

    source_path = os.path.join(settings.incoming_dir, req.category, req.item_name)
    if not os.path.isdir(source_path):
        raise HTTPException(status_code=400, detail="Source folder not found on disk")

    dest_dir = os.path.join(settings.media_dir, "games", "switch", title.title)

    db2 = _SL()
    try:
        job = Job(
            type       = "move",
            category   = req.category,
            item_name  = req.item_name,
            source_path = source_path,
            status     = "pending",
            progress   = 0,
        )
        db2.add(job)
        db2.commit()
        db2.refresh(job)
        job_manager.submit_switch_move(job.id, source_path, title.id, content.id, dest_dir)
        return {"job_id": job.id, "dest_dir": dest_dir}
    finally:
        db2.close()


@router.delete("/switch/content")
def delete_content(item_name: str, db: Session = Depends(_db)):
    c = db.query(SwitchContent).filter(SwitchContent.item_name == item_name).first()
    if not c:
        raise HTTPException(status_code=404, detail="No match found")
    db.delete(c)
    db.commit()
    return {"deleted": item_name}


# ── Helpers ───────────────────────────────────────────────────────────────────

_GAME_EXTS = {".xci", ".nsp", ".nsz"}


def _find_game_file(folder: str) -> tuple[str | None, int | None]:
    """Return (filename, size_bytes) of the largest game file in a folder."""
    best_name, best_size = None, 0
    try:
        for name in os.listdir(folder):
            if os.path.splitext(name)[1].lower() in _GAME_EXTS:
                try:
                    sz = os.path.getsize(os.path.join(folder, name))
                    if sz > best_size:
                        best_name, best_size = name, sz
                except OSError:
                    pass
    except OSError:
        pass
    return best_name, best_size or None


def _cache_cover(key: str, cover_url: str) -> str | None:
    """Download cover art to config_dir/switch_covers/{key}.jpg."""
    try:
        os.makedirs(_COVER_DIR, exist_ok=True)
        safe_key = re.sub(r'[^\w\-]', '_', str(key))
        dest = os.path.join(_COVER_DIR, f"{safe_key}.jpg")
        req = urllib.request.Request(cover_url, headers={"User-Agent": "StavenMediaManager/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                with open(dest, "wb") as f:
                    f.write(resp.read())
                return dest
    except Exception as exc:
        logger.warning(f"Cover download failed for {key}: {exc}")
    return None
