"""
BroadcasTheNet (BTN) client — search via JSON-RPC API, fetch .torrent files.

API docs: https://apidocs.broadcasthe.net/

No external dependencies — uses only Python stdlib (urllib, json).

Credentials via environment variable:
  BTN_API_KEY — API key from your BTN profile → Manage API Keys
"""
import json
import logging
import urllib.request
from dataclasses import dataclass, field

from ..config import settings

logger = logging.getLogger(__name__)

BTN_API = "https://api.broadcasthe.net/"


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BTNResult:
    torrent_id: str
    title: str           # ReleaseName — full scene name e.g. "Futurama.S07E01.720p.WEB-DL…"
    series: str          # Series name only, e.g. "Futurama"
    size_bytes: int
    seeders: int
    leechers: int
    category: str        # Episode | Season | Show
    source: str          # WEB-DL | HDTV | BluRay | …
    resolution: str      # 1080p | 720p | …
    codec: str           # H.264 | H.265 | …
    info_url: str
    pubdate: str = ""
    extra: dict = field(default_factory=dict)


# ── Client ────────────────────────────────────────────────────────────────────

class BTNClient:

    def is_configured(self) -> bool:
        return bool(settings.btn_api_key)

    # ── JSON-RPC transport ────────────────────────────────────────────────────

    def _rpc(self, method: str, *params):
        """POST a JSON-RPC request and return the 'result' value."""
        body = json.dumps({"method": method, "params": list(params), "id": 1}).encode()
        req = urllib.request.Request(
            BTN_API,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "StavenMediaManager/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode()
        except Exception as exc:
            raise RuntimeError(f"BTN API request failed: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            snippet = raw[:300]
            raise RuntimeError(f"BTN API returned invalid JSON: {snippet!r}") from exc

        if data.get("error"):
            raise RuntimeError(f"BTN API error: {data['error']}")

        return data.get("result")

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 50) -> list[BTNResult]:
        """Search BTN via getTorrentsSearch."""
        if not self.is_configured():
            raise RuntimeError("BTN not configured (BTN_API_KEY missing).")

        logger.info(f"BTN search: query={query!r} limit={limit}")
        result = self._rpc("getTorrentsSearch", settings.btn_api_key, {"series": query}, limit)

        torrents = (result or {}).get("Torrents") or {}
        if not isinstance(torrents, dict):
            return []

        results = []
        for tid, t in torrents.items():
            try:
                results.append(self._parse_torrent(tid, t))
            except Exception as exc:
                logger.debug(f"BTN torrent parse error (id={tid}): {exc}")
                continue

        results.sort(key=lambda r: r.seeders, reverse=True)
        logger.info(f"BTN search returned {len(results)} results")
        return results

    def _parse_torrent(self, tid: str, t: dict) -> BTNResult:
        release = t.get("ReleaseName") or t.get("GroupName") or f"Torrent {tid}"
        series  = t.get("Series") or t.get("GroupName") or ""

        try:
            size_bytes = int(t.get("Size") or 0)
        except (ValueError, TypeError):
            size_bytes = 0

        torrent_id = str(t.get("TorrentID") or tid)
        info_url = f"https://broadcasthe.net/torrents.php?id={torrent_id}" if torrent_id else ""

        return BTNResult(
            torrent_id=torrent_id,
            title=release,
            series=series,
            size_bytes=size_bytes,
            seeders=int(t.get("Seeders") or 0),
            leechers=int(t.get("Leechers") or 0),
            category=t.get("Category") or "",
            source=t.get("Source") or "",
            resolution=t.get("Resolution") or "",
            codec=t.get("Codec") or "",
            info_url=info_url,
            pubdate=str(t.get("Time") or ""),
        )

    # ── Download URL resolution ────────────────────────────────────────────────

    def get_torrent_url(self, torrent_id: str) -> str:
        """Resolve a BTN torrent ID to a direct .torrent download URL."""
        logger.info(f"BTN getTorrentsUrl: torrent_id={torrent_id}")
        result = self._rpc("getTorrentsUrl", settings.btn_api_key, int(torrent_id))
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get("DownloadURL") or result.get("URL") or result.get("download") or ""
        raise RuntimeError(f"Unexpected getTorrentsUrl response: {result!r}")

    # ── Fetch bytes ────────────────────────────────────────────────────────────

    def fetch_torrent_bytes(self, torrent_url: str) -> bytes:
        """Download the .torrent file and return raw bytes."""
        req = urllib.request.Request(
            torrent_url,
            headers={"User-Agent": "StavenMediaManager/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except Exception as exc:
            raise RuntimeError(f"Failed to download .torrent: {exc}") from exc

        if len(data) < 10:
            raise RuntimeError(
                f"Downloaded .torrent is suspiciously small ({len(data)} bytes) — auth may have failed."
            )
        if not data.startswith(b"d"):
            snippet = data[:200].decode(errors="replace")
            raise RuntimeError(f"Response does not look like a .torrent file: {snippet!r}")
        return data
