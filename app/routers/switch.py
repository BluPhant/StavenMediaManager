"""
Switch router — game lookup, title/content library management, and push install.

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
  GET  /switch/scan                 — list ROMS folder vs DB
  POST /switch/scan-import          — import unmatched ROMS folders into DB
  GET  /switch/cover-image          — serve local cover art
  GET  /switch/file/{path}          — serve game file (range-request aware, for Awoo HTTP fetch)
  GET  /switch/targets              — list Switch console targets
  POST /switch/targets              — add a Switch target
  DELETE /switch/targets/{id}       — remove a target
  POST /switch/install              — push files to an Awoo console (Tinfoil NET protocol)
"""
import logging
import os
import re
import socket
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import SwitchContent, SwitchTarget, SwitchTitle
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
        "id":           t.id,
        "game_id":      t.game_id,
        "igdb_id":      t.igdb_id,
        "nintendo_id":  t.nintendo_id,
        "title":        t.title,
        "developer":    t.developer,
        "publisher":    t.publisher,
        "description":  t.description,
        "genres":       t.genres,
        "num_players":  t.num_players,
        "release_date": t.release_date,
        "cover_url":    t.cover_url,
        "cover_local":  t.cover_local,
        "library_path": t.library_path,
        "created_at":   t.created_at,
        "updated_at":   t.updated_at,
        "contents":     [_content_to_dict(c) for c in (contents or [])],
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
        # Patch any newly known identifiers and metadata onto the existing record
        if nintendo_id and not title_rec.nintendo_id:
            title_rec.nintendo_id = nintendo_id
        if igdb_id and not title_rec.igdb_id:
            title_rec.igdb_id = igdb_id
        if game_id and not title_rec.game_id:
            title_rec.game_id = game_id
        if req.developer and not title_rec.developer:
            title_rec.developer = req.developer
        if req.publisher and not title_rec.publisher:
            title_rec.publisher = req.publisher
        if req.cover_url and not title_rec.cover_url:
            title_rec.cover_url = req.cover_url
            key = game_id or (f"igdb{igdb_id}" if igdb_id else "cover")
            cover_local = _cache_cover(key, req.cover_url)
            if cover_local:
                title_rec.cover_local = cover_local

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

    dest_dir = os.path.join(settings.media_dir, "games", "switch", "ROMS", title.title)

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


# ── Library scan ──────────────────────────────────────────────────────────────

_ROMS_SUBDIR = os.path.join("games", "switch", "ROMS")
_GAME_EXTS_SET = {".xci", ".nsp", ".nsz"}


def _roms_dir() -> str:
    return os.path.join(settings.media_dir, "games", "switch", "ROMS")


@router.get("/switch/scan")
def scan_roms(db: Session = Depends(_db)):
    """List every subfolder in games/switch/ROMS and report DB match status."""
    import json as _json

    roms = _roms_dir()
    if not os.path.isdir(roms):
        return {"roms_dir": roms, "found": [], "total": 0, "matched": 0, "unmatched": 0}

    all_titles = db.query(SwitchTitle).all()
    by_title   = {t.title.lower(): t for t in all_titles}
    by_path    = {(t.library_path or "").rstrip("/"): t for t in all_titles}

    found = []
    for name in sorted(os.listdir(roms)):
        entry = os.path.join(roms, name)
        if not os.path.isdir(entry):
            continue

        rec = by_title.get(name.lower()) or by_path.get(entry.rstrip("/"))

        meta = None
        mpath = os.path.join(entry, "metadata.json")
        if os.path.isfile(mpath):
            try:
                with open(mpath, encoding="utf-8") as f:
                    meta = _json.load(f)
            except Exception:
                pass

        game_files = []
        try:
            for fname in os.listdir(entry):
                if os.path.splitext(fname)[1].lower() in _GAME_EXTS_SET:
                    try:
                        sz = os.path.getsize(os.path.join(entry, fname))
                    except OSError:
                        sz = 0
                    game_files.append({"name": fname, "size": sz})
        except OSError:
            pass

        has_cover = os.path.isfile(os.path.join(entry, "cover.jpg"))

        found.append({
            "folder_name":  name,
            "library_path": entry,
            "matched":      rec is not None,
            "title_id":     rec.id if rec else None,
            "title":        (rec.title if rec else None) or (meta and meta.get("title")) or name,
            "cover_url":    rec.cover_url if rec else (meta and meta.get("cover_url")),
            "cover_local":  rec.cover_local if rec else None,
            "has_cover":    has_cover,
            "publisher":    rec.publisher if rec else (meta and meta.get("publisher")),
            "developer":    rec.developer if rec else (meta and meta.get("developer")),
            "description":  rec.description if rec else (meta and meta.get("description")),
            "genres":       rec.genres if rec else (meta and meta.get("genres")),
            "num_players":  rec.num_players if rec else (meta and meta.get("num_players")),
            "release_date": rec.release_date if rec else (meta and meta.get("release_date")),
            "igdb_id":      rec.igdb_id if rec else (meta and meta.get("igdb_id")),
            "game_id":      rec.game_id if rec else None,
            "file_count":   len(game_files),
            "game_files":   game_files,
        })

    matched = sum(1 for f in found if f["matched"])
    return {
        "roms_dir": roms,
        "found": found,
        "total": len(found),
        "matched": matched,
        "unmatched": len(found) - matched,
    }


@router.post("/switch/scan-import")
def scan_import(db: Session = Depends(_db)):
    """
    Walk games/switch/ROMS and create SwitchTitle records for any unmatched folder.
    Attempts an IGDB search for each new game to populate metadata + cover art.
    Already-matched titles have their library_path / cover_local back-filled if missing.
    """
    import json as _json
    import shutil as _shutil
    import time

    roms = _roms_dir()
    if not os.path.isdir(roms):
        return {"imported": 0, "skipped": 0, "errors": []}

    all_titles = db.query(SwitchTitle).all()
    by_title   = {t.title.lower(): t for t in all_titles}
    by_path    = {(t.library_path or "").rstrip("/"): t for t in all_titles}

    imported, skipped, errors = 0, 0, []

    for name in sorted(os.listdir(roms)):
        entry = os.path.join(roms, name)
        if not os.path.isdir(entry):
            continue

        rec = by_title.get(name.lower()) or by_path.get(entry.rstrip("/"))

        if rec:
            # Back-fill missing fields on existing record
            changed = False
            if not rec.library_path:
                rec.library_path = entry
                changed = True
            cov_path = os.path.join(entry, "cover.jpg")
            if not rec.cover_local and os.path.isfile(cov_path):
                rec.cover_local = cov_path
                changed = True
            # Back-fill IGDB-sourced metadata if missing
            needs_enrich = not rec.description and settings.igdb_client_id and settings.igdb_client_secret
            if needs_enrich:
                search_query = rec.title or name
                try:
                    results = igdb_svc.search_games(
                        search_query, settings.igdb_client_id, settings.igdb_client_secret, limit=1
                    )
                    if results:
                        r = results[0]
                        if r.get("description") and not rec.description:
                            rec.description = r["description"]
                            changed = True
                        if r.get("genres") and not rec.genres:
                            rec.genres = r["genres"]
                            changed = True
                        if r.get("num_players") and not rec.num_players:
                            rec.num_players = r["num_players"]
                            changed = True
                        if r.get("release_date") and not rec.release_date:
                            rec.release_date = r["release_date"]
                            changed = True
                        if r.get("igdb_id") and not rec.igdb_id:
                            rec.igdb_id = r["igdb_id"]
                            changed = True
                    time.sleep(0.25)
                except Exception as exc:
                    logger.warning(f"IGDB enrich failed for {name!r}: {exc}")
            if changed:
                db.commit()
            skipped += 1
            continue

        # --- New game ---
        meta = None
        mpath = os.path.join(entry, "metadata.json")
        if os.path.isfile(mpath):
            try:
                with open(mpath, encoding="utf-8") as f:
                    meta = _json.load(f)
            except Exception:
                pass

        title_str = (meta and meta.get("title")) or name
        igdb_id   = meta and meta.get("igdb_id")
        cover_url = meta and meta.get("cover_url")
        publisher = meta and meta.get("publisher")
        developer = meta and meta.get("developer")

        description = meta and meta.get("description")
        genres      = meta and meta.get("genres")
        num_players = meta and meta.get("num_players")
        release_date = meta and meta.get("release_date")

        # IGDB search if credentials available
        if not igdb_id and settings.igdb_client_id and settings.igdb_client_secret:
            try:
                results = igdb_svc.search_games(
                    name, settings.igdb_client_id, settings.igdb_client_secret, limit=1
                )
                if results:
                    r = results[0]
                    igdb_id      = r.get("igdb_id") or igdb_id
                    cover_url    = r.get("cover_url") or cover_url
                    publisher    = r.get("publisher") or publisher
                    developer    = r.get("developer") or developer
                    title_str    = r.get("title") or title_str
                    description  = r.get("description") or description
                    genres       = r.get("genres") or genres
                    num_players  = r.get("num_players") or num_players
                    release_date = r.get("release_date") or release_date
                time.sleep(0.25)   # stay well within IGDB rate limit
            except Exception as exc:
                logger.warning(f"IGDB search failed for {name!r}: {exc}")

        # Cover art: prefer existing library copy, else download
        cover_local = None
        cov_path = os.path.join(entry, "cover.jpg")
        if os.path.isfile(cov_path):
            cover_local = cov_path
        elif cover_url:
            key = f"igdb{igdb_id}" if igdb_id else re.sub(r'[^\w\-]', '_', name)
            cached = _cache_cover(key, cover_url)
            if cached:
                cover_local = cached
                try:
                    _shutil.copy2(cached, cov_path)
                    cover_local = cov_path
                except Exception:
                    pass

        try:
            new_rec = SwitchTitle(
                game_id      = None,
                igdb_id      = igdb_id,
                nintendo_id  = None,
                title        = title_str,
                developer    = developer,
                publisher    = publisher,
                description  = description,
                genres       = genres,
                num_players  = num_players,
                release_date = release_date,
                cover_url    = cover_url,
                cover_local  = cover_local,
                library_path = entry,
            )
            db.add(new_rec)
            db.commit()
            by_title[title_str.lower()] = new_rec
            imported += 1
        except Exception as exc:
            db.rollback()
            errors.append({"folder": name, "error": str(exc)})
            logger.error(f"scan-import failed for {name!r}: {exc}")

    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.get("/switch/file/{file_path:path}")
def serve_game_file(file_path: str):
    """
    Serve a game file from the ROMS library with range-request support.
    Called by Awoo/Tinfoil during network install to stream the actual file bytes.
    """
    roms = os.path.normpath(_roms_dir())
    full = os.path.normpath(os.path.join(roms, file_path))
    # Prevent path traversal
    if not full.startswith(roms + os.sep) and full != roms:
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full, media_type="application/octet-stream")


# ── Switch targets ─────────────────────────────────────────────────────────────

class TargetRequest(BaseModel):
    name:       str
    ip_address: str
    port:       int = 2000


@router.get("/switch/targets")
def list_targets(db: Session = Depends(_db)):
    return [
        {"id": t.id, "name": t.name, "ip_address": t.ip_address, "port": t.port}
        for t in db.query(SwitchTarget).order_by(SwitchTarget.name).all()
    ]


@router.post("/switch/targets", status_code=201)
def create_target(req: TargetRequest, db: Session = Depends(_db)):
    t = SwitchTarget(name=req.name, ip_address=req.ip_address, port=req.port)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "name": t.name, "ip_address": t.ip_address, "port": t.port}


@router.delete("/switch/targets/{target_id}")
def delete_target(target_id: int, db: Session = Depends(_db)):
    t = db.query(SwitchTarget).filter(SwitchTarget.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Target not found")
    db.delete(t)
    db.commit()
    return {"deleted": target_id}


# ── Push install ───────────────────────────────────────────────────────────────

class InstallRequest(BaseModel):
    title_id:    int
    target_id:   int
    content_ids: list[int] | None = None   # None = send all content for the title


@router.post("/switch/install")
def install_to_switch(req: InstallRequest, db: Session = Depends(_db)):
    """
    Push game files to a Switch running Awoo/Tinfoil in Network Install mode.
    Uses the Tinfoil NET protocol (TCP port 2000): sends file URLs to the Switch,
    which then fetches each file via HTTP from this server.
    """
    target = db.query(SwitchTarget).filter(SwitchTarget.id == req.target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Switch target not found")

    title = db.query(SwitchTitle).filter(SwitchTitle.id == req.title_id).first()
    if not title:
        raise HTTPException(status_code=404, detail="Title not found")

    if req.content_ids:
        contents = db.query(SwitchContent).filter(
            SwitchContent.id.in_(req.content_ids),
            SwitchContent.title_id == req.title_id,
        ).all()
    else:
        contents = db.query(SwitchContent).filter(
            SwitchContent.title_id == req.title_id
        ).all()

    # Build HTTP URLs the Switch will use to fetch each file.
    # Use the external host:port the Switch can actually reach (not container-internal port).
    host_ip   = settings.switch_host_ip or "172.16.1.40"
    host_port = settings.switch_host_port  # e.g. 8088 when Docker maps host:8088→container:8080
    roms      = os.path.normpath(_roms_dir())
    file_urls: list[str] = []

    def _add_file(fpath: str) -> None:
        fpath = os.path.normpath(fpath)
        if os.path.isfile(fpath):
            rel = os.path.relpath(fpath, roms).replace("\\", "/")
            url = f"{host_ip}:{host_port}/api/switch/file/{urllib.parse.quote(rel, safe='/')}"
            file_urls.append(url)

    # Primary: file paths stored on SwitchContent records
    for c in contents:
        if c.library_path:
            _add_file(c.library_path)

    # Fallback: scan the title's library folder directly (covers scan-imported games
    # that have no SwitchContent records, or content records with missing library_path)
    if not file_urls and title.library_path and os.path.isdir(title.library_path):
        for fname in sorted(os.listdir(title.library_path)):
            if os.path.splitext(fname)[1].lower() in {".nsp", ".xci", ".nsz"}:
                _add_file(os.path.join(title.library_path, fname))

    if not file_urls:
        raise HTTPException(status_code=400, detail="No game files found on disk for this title")

    try:
        _send_to_awoo(target.ip_address, target.port, file_urls)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not connect to {target.name} ({target.ip_address}:{target.port}) — "
                   f"make sure Awoo is open in Network Install mode. Error: {exc}",
        )

    logger.info(f"Install initiated: {title.title} → {target.name} ({len(file_urls)} file(s))")
    return {"title": title.title, "target": target.name, "files_sent": len(file_urls)}


@router.get("/switch/cover-image")
def cover_image(id: int = None, title: str = None, db: Session = Depends(_db)):
    """Serve a game's cached cover art from disk."""
    from fastapi.responses import FileResponse
    from sqlalchemy import func as _func

    rec = None
    if id:
        rec = db.query(SwitchTitle).filter(SwitchTitle.id == id).first()
    elif title:
        rec = db.query(SwitchTitle).filter(
            _func.lower(SwitchTitle.title) == title.lower()
        ).first()

    paths: list[str] = []
    if rec and rec.cover_local:
        paths.append(rec.cover_local)
    if rec and rec.library_path:
        paths.append(os.path.join(rec.library_path, "cover.jpg"))
    if rec and rec.title:
        paths.append(os.path.join(_roms_dir(), rec.title, "cover.jpg"))

    for p in paths:
        if p and os.path.isfile(p):
            return FileResponse(p, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="Cover not found")


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


def _send_to_awoo(ip: str, port: int, file_urls: list[str]) -> None:
    """
    Tinfoil NET / Awoo network install protocol (verified against NS-USBloader source).
    Connects to Switch on TCP port 2000 and sends the list of file locations.
    The Switch then fetches each via HTTP to perform the install.

    Wire format:
      4 bytes  — byte length of payload as big-endian uint32 (Java ByteBuffer default)
      payload  — newline-separated UTF-8 strings, each "HOST:PORT/path\n"
    """
    payload = b""
    for url in file_urls:
        payload += url.encode("utf-8") + b"\n"

    header = len(payload).to_bytes(4, "big")

    with socket.create_connection((ip, port), timeout=10) as sock:
        sock.sendall(header + payload)
        sock.settimeout(3)
        try:
            sock.recv(256)
        except (socket.timeout, OSError):
            pass


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
