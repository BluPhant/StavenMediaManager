"""
About endpoint — version info and live connection health checks.

GET /api/about
  Returns build version, revision, build date, and parallel connection
  status checks for every configured integration (Plex, rTorrent, IPT,
  TMDB, BTN).  All checks run concurrently with individual timeouts so
  the endpoint completes in roughly the time of the slowest check.
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter

from ..config import settings

router = APIRouter(prefix="/about", tags=["about"])


# ── Per-service health checks ─────────────────────────────────────────────────

def _check_plex() -> dict:
    if not (settings.plex_url and settings.plex_token):
        return {"ok": False, "configured": False, "detail": "Not configured"}
    try:
        url = f"{settings.plex_url.rstrip('/')}/identity?X-Plex-Token={settings.plex_token}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=8) as resp:
            ms = int((time.monotonic() - t0) * 1000)
            data = json.loads(resp.read())
        version = data.get("MediaContainer", {}).get("version", "")
        return {"ok": True, "configured": True, "detail": f"Plex {version}", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_rtorrent() -> dict:
    from ..services.sources.rtorrent import RtorrentSource
    from ..config import settings as s
    has_creds = bool(s.rtorrent_url and s.rtorrent_user and (s.rtorrent_ftp_host or s.rtorrent_ssh_host))
    if not has_creds:
        return {"ok": False, "configured": False, "detail": "Not configured"}
    if not s.rtorrent_enabled:
        return {"ok": True, "configured": True, "inactive": True, "detail": "Disabled"}
    rt = RtorrentSource()
    # If qBittorrent is the active source, don't connect to rTorrent — just show inactive
    from ..services.sources.qbittorrent import QbittorrentSource
    if QbittorrentSource().is_configured():
        return {"ok": True, "configured": True, "inactive": True, "detail": "Inactive — qBittorrent is active"}
    try:
        proxy = rt._proxy()
        t0 = time.monotonic()
        version = proxy.system.client_version()
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": f"rTorrent {version}", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_qbittorrent() -> dict:
    from ..services.sources.qbittorrent import QbittorrentSource, _QbtClient
    from ..config import settings as s
    has_creds = bool(s.qbittorrent_url and s.qbittorrent_user and s.qbittorrent_ssh_host)
    if not has_creds:
        return {"ok": False, "configured": False, "detail": "Not configured"}
    if not s.qbittorrent_enabled:
        return {"ok": True, "configured": True, "inactive": True, "detail": "Disabled"}
    try:
        client = _QbtClient()
        t0 = time.monotonic()
        raw = client.get("/api/v2/app/version")
        ms = int((time.monotonic() - t0) * 1000)
        version = raw.decode(errors="replace").strip()
        return {"ok": True, "configured": True, "detail": f"qBittorrent {version}", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_ipt() -> dict:
    from ..services.iptorrents import IPTorrentsClient
    ipt = IPTorrentsClient()
    if not ipt.is_configured():
        return {"ok": False, "configured": False, "detail": "Not configured"}
    try:
        url = ipt._rss_url(query="", category="all")
        t0 = time.monotonic()
        ipt._fetch_xml(url, timeout=8)
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": "Authenticated", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_tmdb() -> dict:
    if not settings.tmdb_api_key:
        return {"ok": False, "configured": False, "detail": "Not configured"}
    try:
        url = f"https://api.themoviedb.org/3/configuration?api_key={settings.tmdb_api_key}"
        t0 = time.monotonic()
        with urllib.request.urlopen(url, timeout=8) as resp:
            ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": "API key valid", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_btn() -> dict:
    from ..services.btn import BTNClient
    btn = BTNClient()
    if not btn.is_configured():
        return {"ok": False, "configured": False, "detail": "Not configured"}
    try:
        t0 = time.monotonic()
        btn._rpc("getTorrentsSearch", settings.btn_api_key, {"series": ""}, 1, 0)
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": "API key valid", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_igdb() -> dict:
    if not (settings.igdb_client_id and settings.igdb_client_secret):
        return {"ok": False, "configured": False, "detail": "Not configured"}
    try:
        from ..services.igdb import _get_token
        t0 = time.monotonic()
        _get_token(settings.igdb_client_id, settings.igdb_client_secret)
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": "Token valid", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_audible() -> dict:
    try:
        import urllib.parse
        url = "https://api.audible.com/1.0/catalog/products?title=test&num_results=1&response_groups=product_desc"
        req = urllib.request.Request(url, headers={"User-Agent": "StavenMediaManager/1.0"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=8) as resp:
            ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": "Catalog reachable", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


def _check_discogs() -> dict:
    from ..services.discogs import DiscogsClient
    client = DiscogsClient()
    if not client.is_configured():
        return {"ok": False, "configured": False, "detail": "Not configured"}
    try:
        url = "https://api.discogs.com/database/search?q=test&type=release&per_page=1"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Discogs token={settings.discogs_token}",
            "User-Agent": "StavenMediaManager/1.0",
        })
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=8) as resp:
            ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": "Token valid", "ms": ms}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": str(exc)}


# ── Endpoint ──────────────────────────────────────────────────────────────────

_CHECKS = {
    "plex":         _check_plex,
    "rtorrent":     _check_rtorrent,
    "qbittorrent":  _check_qbittorrent,
    "iptorrents":   _check_ipt,
    "tmdb":         _check_tmdb,
    "btn":          _check_btn,
    "igdb":         _check_igdb,
    "discogs":      _check_discogs,
    "audible":      _check_audible,
}


@router.get("")
def get_about():
    checks: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(_CHECKS)) as ex:
        futures = {ex.submit(fn): name for name, fn in _CHECKS.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                checks[name] = future.result()
            except Exception as exc:
                checks[name] = {"ok": False, "configured": True, "detail": str(exc)}

    rev = settings.app_revision
    return {
        "version":    settings.app_version,
        "revision":   rev[:7] if rev else "",
        "build_date": settings.app_build_date,
        "checks":     checks,
    }
