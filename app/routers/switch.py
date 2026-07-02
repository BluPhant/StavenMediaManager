"""
Switch router — GameTDB lookup, title/content library management.

Endpoints:
  GET  /switch/lookup/{game_id}    — fetch game info from GameTDB
  GET  /switch/title/{game_id}     — get SwitchTitle from local DB
  GET  /switch/titles              — list all titles in library
  GET  /switch/content             — get SwitchContent record(s) for an item_name
  POST /switch/match               — confirm match: upsert SwitchTitle + SwitchContent
  DELETE /switch/content           — remove a SwitchContent record (not the title)
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import SwitchContent, SwitchTitle
from ..services import gametdb as tdb

logger = logging.getLogger(__name__)
router = APIRouter(tags=["switch"])

_COVER_DIR = os.path.join(settings.config_dir, "switch_covers")


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _title_to_dict(t: SwitchTitle, contents: list[SwitchContent] | None = None) -> dict:
    return {
        "id": t.id,
        "game_id": t.game_id,
        "title": t.title,
        "developer": t.developer,
        "publisher": t.publisher,
        "cover_url": t.cover_url,
        "cover_local": t.cover_local,
        "library_path": t.library_path,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "contents": [_content_to_dict(c) for c in (contents or [])],
    }


def _content_to_dict(c: SwitchContent) -> dict:
    return {
        "id": c.id,
        "title_id": c.title_id,
        "item_name": c.item_name,
        "content_type": c.content_type,
        "version": c.version,
        "dlc_name": c.dlc_name,
        "filename": c.filename,
        "file_size": c.file_size,
        "library_path": c.library_path,
        "created_at": c.created_at,
    }


# ── GameTDB lookup ────────────────────────────────────────────────────────────

@router.get("/switch/lookup/{game_id}")
def lookup_game(game_id: str):
    """
    Fetch game metadata from GameTDB (live web scrape).
    Also tries to auto-detect the ID from the item_name query param if provided.
    """
    game = tdb.get_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found on GameTDB")
    return {
        "game_id": game.game_id,
        "title": game.title,
        "developer": game.developer,
        "publisher": game.publisher,
        "cover_url": game.cover_url,
    }


@router.get("/switch/detect")
def detect_id(item_name: str, category: str = "switch-games"):
    """
    Try to auto-detect the GameTDB ID from files inside an incoming item folder.
    Returns {game_id, confidence} or 404 if nothing found.
    """
    folder = os.path.join(settings.incoming_dir, category, item_name)
    if not os.path.isdir(folder):
        raise HTTPException(status_code=404, detail="Item folder not found")

    # Check files in the folder for a recognisable game ID
    detected = None
    try:
        for fname in os.listdir(folder):
            ext = os.path.splitext(fname)[1].lower()
            if ext in (".xci", ".nsp", ".nsz"):
                candidate = tdb.extract_id_from_filename(fname)
                if candidate:
                    detected = candidate
                    break
    except OSError:
        pass

    # Fall back: try to extract ID from the folder name itself
    if not detected:
        for fname in [item_name]:
            candidate = tdb.extract_id_from_filename(fname)
            if candidate:
                detected = candidate
                break

    if not detected:
        # Derive a human-readable title guess from folder name
        guessed_title = tdb.clean_title_from_folder(item_name)
        content_type, version, dlc_name = tdb.parse_content_type(item_name)
        return {
            "game_id": None,
            "guessed_title": guessed_title,
            "content_type": content_type,
            "version": version,
            "dlc_name": dlc_name,
        }

    content_type, version, dlc_name = tdb.parse_content_type(item_name)
    return {
        "game_id": detected,
        "guessed_title": tdb.clean_title_from_folder(item_name),
        "content_type": content_type,
        "version": version,
        "dlc_name": dlc_name,
    }


# ── Local library ─────────────────────────────────────────────────────────────

@router.get("/switch/titles")
def list_titles(db: Session = Depends(_db)):
    """List all Switch titles in the local library."""
    titles = db.query(SwitchTitle).order_by(SwitchTitle.title).all()
    result = []
    for t in titles:
        contents = db.query(SwitchContent).filter(SwitchContent.title_id == t.id).all()
        result.append(_title_to_dict(t, contents))
    return result


@router.get("/switch/title/{game_id}")
def get_title(game_id: str, db: Session = Depends(_db)):
    """Get a single SwitchTitle with its contents from the local DB."""
    t = db.query(SwitchTitle).filter(SwitchTitle.game_id == game_id.upper()).first()
    if not t:
        raise HTTPException(status_code=404, detail="Title not in local library")
    contents = db.query(SwitchContent).filter(SwitchContent.title_id == t.id).all()
    return _title_to_dict(t, contents)


@router.get("/switch/content")
def get_content_for_item(item_name: str, db: Session = Depends(_db)):
    """Get the SwitchContent record for a specific incoming item (if matched)."""
    c = db.query(SwitchContent).filter(SwitchContent.item_name == item_name).first()
    if not c:
        raise HTTPException(status_code=404, detail="No match for this item")
    t = db.query(SwitchTitle).filter(SwitchTitle.id == c.title_id).first()
    return {**_content_to_dict(c), "title": _title_to_dict(t) if t else None}


class MatchRequest(BaseModel):
    category:     str
    item_name:    str
    game_id:      str
    content_type: str = "base"   # base | update | dlc
    version:      str | None = None
    dlc_name:     str | None = None


@router.post("/switch/match", status_code=201)
def save_match(req: MatchRequest, db: Session = Depends(_db)):
    """
    Confirm a match between an incoming item and a GameTDB game.
    Upserts SwitchTitle (fetches metadata from GameTDB if new).
    Creates / updates the SwitchContent record for this item.
    """
    game_id = req.game_id.strip().upper()

    # Upsert SwitchTitle
    title_rec = db.query(SwitchTitle).filter(SwitchTitle.game_id == game_id).first()
    if not title_rec:
        game = tdb.get_game(game_id)
        if not game:
            raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found on GameTDB")

        # Download and cache cover art
        cover_local = _cache_cover(game_id, game.cover_url)

        title_rec = SwitchTitle(
            game_id=game_id,
            title=game.title,
            developer=game.developer or None,
            publisher=game.publisher or None,
            cover_url=game.cover_url or None,
            cover_local=cover_local,
        )
        db.add(title_rec)
        db.flush()

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
    category:   str
    item_name:  str


@router.post("/switch/move", status_code=201)
def move_to_library(req: MoveRequest, db: Session = Depends(_db)):
    """
    Submit a move job for a confirmed Switch item.
    Requires a SwitchContent record (created via /switch/match) to already exist.
    """
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
            type="move",
            category=req.category,
            item_name=req.item_name,
            source_path=source_path,
            status="pending",
            progress=0,
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
    """Remove the SwitchContent record for an item (does not delete the SwitchTitle)."""
    c = db.query(SwitchContent).filter(SwitchContent.item_name == item_name).first()
    if not c:
        raise HTTPException(status_code=404, detail="No match found")
    db.delete(c)
    db.commit()
    return {"deleted": item_name}


# ── helpers ───────────────────────────────────────────────────────────────────

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


def _cache_cover(game_id: str, cover_url: str) -> str | None:
    """Download cover art and save to config_dir/switch_covers/{game_id}.jpg."""
    try:
        os.makedirs(_COVER_DIR, exist_ok=True)
        dest = os.path.join(_COVER_DIR, f"{game_id}.jpg")
        data = tdb.fetch_cover_bytes(game_id)
        if data:
            with open(dest, "wb") as f:
                f.write(data)
            return dest
    except Exception as exc:
        logger.warning(f"Cover download failed for {game_id}: {exc}")
    return None
