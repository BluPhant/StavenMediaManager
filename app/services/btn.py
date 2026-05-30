"""
BroadcasTheNet (BTN) client — search via JSON API, fetch .torrent files.

No external dependencies — uses only Python stdlib (urllib, json).

Credentials via environment variable:
  BTN_API_KEY — API key from your BTN profile page (Manage API Keys)
"""
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ..config import settings

logger = logging.getLogger(__name__)

BTN_API = "https://broadcasthe.net/api.php"


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BTNResult:
    torrent_id: str
    title: str           # series + group name, e.g. "Breaking Bad S01E01 720p WEB-DL"
    series: str          # series name only, e.g. "Breaking Bad"
    size_bytes: int
    seeders: int
    leechers: int
    category: str        # Episode | Season | Show
    source: str          # WEB-DL | HDTV | BluRay | ...
    resolution: str      # 1080p | 720p | ...
    codec: str           # H.264 | H.265 | ...
    torrent_url: str     # direct download URL (auth embedded by BTN)
    info_url: str        # BTN details page
    pubdate: str = ""
    extra: dict = field(default_factory=dict)


# ── Client ────────────────────────────────────────────────────────────────────

class BTNClient:

    def is_configured(self) -> bool:
        return bool(settings.btn_api_key)

    def _api(self, action: str, params: dict) -> dict:
        p = {"apikey": settings.btn_api_key, "action": action, **params}
        url = f"{BTN_API}?{urllib.parse.urlencode(p)}"
        req = urllib.request.Request(url, headers={"User-Agent": "StavenMediaManager/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode()
        except Exception as exc:
            raise RuntimeError(f"BTN API request failed: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"BTN API returned invalid JSON: {raw[:200]!r}") from exc

    def search(self, query: str, limit: int = 50) -> list[BTNResult]:
        """Search BTN via the getTorrents API action."""
        if not self.is_configured():
            raise RuntimeError("BTN not configured (BTN_API_KEY missing).")

        logger.info(f"BTN search: query={query!r} limit={limit}")
        data = self._api("getTorrents", {
            "searchstr": query,
            "results":   str(limit),
            "offset":    "0",
        })

        # Empty results come back as a list, not a dict
        torrents = data.get("torrents") or {}
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
        series = t.get("Series") or t.get("SeriesName") or ""
        group  = t.get("GroupName") or ""
        # Build display title: "Series GroupName" without redundant repetition
        if group and series and series.lower() not in group.lower():
            title = f"{series} {group}".strip()
        elif group:
            title = group
        else:
            title = series or f"Torrent {tid}"

        try:
            size_bytes = int(t.get("Size") or 0)
        except (ValueError, TypeError):
            size_bytes = 0

        return BTNResult(
            torrent_id=str(tid),
            title=title,
            series=series,
            size_bytes=size_bytes,
            seeders=int(t.get("Seeders") or 0),
            leechers=int(t.get("Leechers") or 0),
            category=t.get("Category") or "",
            source=t.get("Source") or "",
            resolution=t.get("Resolution") or "",
            codec=t.get("Codec") or "",
            torrent_url=t.get("DownloadURL") or t.get("TorrentLink") or "",
            info_url=t.get("DetailsLink") or "",
            pubdate=str(t.get("Time") or ""),
        )

    def fetch_torrent_bytes(self, torrent_url: str) -> bytes:
        """Download the .torrent file from BTN and return raw bytes."""
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
