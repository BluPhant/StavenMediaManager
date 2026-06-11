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
    rt = RtorrentSource()
    if not rt.is_configured():
        return {"ok": False, "configured": False, "detail": "Not configured"}
    try:
        proxy = rt._proxy()
        t0 = time.monotonic()
        version = proxy.system.client_version()
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "configured": True, "detail": f"rTorrent {version}", "ms": ms}
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


# ── Endpoint ──────────────────────────────────────────────────────────────────

_CHECKS = {
    "plex":       _check_plex,
    "rtorrent":   _check_rtorrent,
    "iptorrents": _check_ipt,
    "tmdb":       _check_tmdb,
    "btn":        _check_btn,
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
