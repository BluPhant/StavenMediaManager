"""
IGDB client — Nintendo Switch game search via the IGDB/Twitch API.
Requires IGDB_CLIENT_ID and IGDB_CLIENT_SECRET env vars.
Platform 130 = Nintendo Switch.
"""
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_IGDB_BASE = "https://api.igdb.com/v4"
_SWITCH_PLATFORM = 130

_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_token(client_id: str, client_secret: str) -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    })
    req = urllib.request.Request(
        _TWITCH_TOKEN_URL,
        data=params.encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
    _token_cache["token"] = body["access_token"]
    _token_cache["expires_at"] = now + body.get("expires_in", 3600)
    logger.info("IGDB: token refreshed")
    return _token_cache["token"]


def _post(endpoint: str, body: str, client_id: str, client_secret: str) -> list:
    token = _get_token(client_id, client_secret)
    req = urllib.request.Request(
        f"{_IGDB_BASE}/{endpoint}",
        data=body.encode(),
        headers={
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _to_dict(r: dict) -> dict:
    cover_url = None
    cover = r.get("cover")
    if isinstance(cover, dict) and cover.get("image_id"):
        cover_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover['image_id']}.jpg"

    release_date = year = None
    ts = r.get("first_release_date")
    if ts:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        year = dt.year
        release_date = dt.strftime("%Y-%m-%d")

    publisher = developer = None
    for c in (r.get("involved_companies") or []):
        if not isinstance(c, dict):
            continue
        cname = (c.get("company") or {}).get("name")
        if not cname:
            continue
        if c.get("publisher") and not publisher:
            publisher = cname
        if c.get("developer") and not developer:
            developer = cname

    genres = ", ".join(
        g["name"] for g in (r.get("genres") or []) if isinstance(g, dict) and g.get("name")
    ) or None

    # Player count: prefer multiplayer_modes max, fall back to game_modes presence
    num_players = None
    mm = r.get("multiplayer_modes") or []
    if mm and isinstance(mm, list):
        offline_max = max((m.get("offlinemax") or 1 for m in mm if isinstance(m, dict)), default=1)
        online_max  = max((m.get("onlinemax")  or 0 for m in mm if isinstance(m, dict)), default=0)
        total_max = max(offline_max, online_max)
        num_players = "1" if total_max <= 1 else f"1–{total_max}"
    else:
        mode_names = [m["name"] for m in (r.get("game_modes") or []) if isinstance(m, dict) and m.get("name")]
        has_multi = any("multi" in n.lower() or "co-op" in n.lower() for n in mode_names)
        if mode_names:
            num_players = "1+" if has_multi else "1"

    return {
        "igdb_id":      r["id"],
        "title":        r.get("name", ""),
        "cover_url":    cover_url,
        "year":         year,
        "release_date": release_date,
        "publisher":    publisher,
        "developer":    developer,
        "description":  r.get("summary") or None,
        "genres":       genres,
        "num_players":  num_players,
    }


def search_games(query: str, client_id: str, client_secret: str, limit: int = 10) -> list[dict]:
    body = (
        f'search "{query}"; '
        f'fields id,name,cover.image_id,first_release_date,'
        f'involved_companies.company.name,involved_companies.publisher,involved_companies.developer,'
        f'summary,genres.name,game_modes.name,'
        f'multiplayer_modes.offlinemax,multiplayer_modes.onlinemax; '
        f'where platforms = ({_SWITCH_PLATFORM}); '
        f'limit {limit};'
    )
    return [_to_dict(r) for r in _post("games", body, client_id, client_secret)]
