"""
Discogs API client — search releases and fetch full release metadata.

Credentials: DISCOGS_TOKEN env var.
Get a personal access token at https://www.discogs.com/settings/developers
(free account, read-only access, 60 req/min).

No third-party dependencies — stdlib urllib only.
"""
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ..config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.discogs.com"
_UA   = "StavenMediaManager/1.0 +https://github.com/BluPhant/StavenMediaManager"

# Discogs appends " (N)" to disambiguate artists with the same name
_DISAMBIG_RE = re.compile(r"\s*\(\d+\)\s*$")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DiscogsResult:
    """Lightweight result from the search endpoint."""
    id:          int
    artist:      str
    title:       str
    year:        int | None
    label:       str | None
    format:      str | None   # e.g. "Vinyl, LP"
    thumb:       str | None   # small thumbnail URL
    cover_image: str | None   # larger image URL
    country:     str | None
    genres:      list[str] = field(default_factory=list)
    styles:      list[str] = field(default_factory=list)


@dataclass
class DiscogsTrack:
    position: str
    title:    str
    duration: str | None


@dataclass
class DiscogsRelease:
    """Full release detail from /releases/{id}."""
    id:        int
    artist:    str
    title:     str
    year:      int | None
    label:     str | None
    catno:     str | None
    cover_url: str | None   # full-resolution primary image URL
    country:   str | None
    genres:    list[str]
    styles:    list[str]
    tracks:    list[DiscogsTrack]


# ── Client ────────────────────────────────────────────────────────────────────

class DiscogsClient:

    def is_configured(self) -> bool:
        return bool(settings.discogs_token)

    def _get(self, path: str, params: dict | None = None, timeout: int = 15) -> dict:
        url = f"{_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Authorization": f"Discogs token={settings.discogs_token}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read())

    def search(self, artist: str = "", album: str = "", limit: int = 10) -> list[DiscogsResult]:
        if not self.is_configured():
            raise RuntimeError("Discogs not configured (DISCOGS_TOKEN missing).")
        params: dict = {"type": "release", "per_page": limit}
        if artist:
            params["artist"] = artist
        if album:
            params["release_title"] = album
        data = self._get("/database/search", params)
        results = []
        for r in (data.get("results") or []):
            full_title = r.get("title", "")
            parts = full_title.split(" - ", 1)
            r_artist = parts[0].strip() if len(parts) > 1 else artist
            r_album  = parts[1].strip() if len(parts) > 1 else full_title
            labels   = r.get("label") or []
            year_s   = r.get("year")
            year     = int(year_s) if year_s and str(year_s).isdigit() else None
            results.append(DiscogsResult(
                id           = r["id"],
                artist       = r_artist,
                title        = r_album,
                year         = year,
                label        = labels[0] if labels else None,
                format       = ", ".join(r.get("format") or []),
                thumb        = r.get("thumb") or None,
                cover_image  = r.get("cover_image") or None,
                country      = r.get("country") or None,
                genres       = r.get("genre") or [],
                styles       = r.get("style") or [],
            ))
        return results

    def get_release(self, release_id: int) -> DiscogsRelease:
        if not self.is_configured():
            raise RuntimeError("Discogs not configured (DISCOGS_TOKEN missing).")
        r = self._get(f"/releases/{release_id}")

        # Artist: strip disambiguation suffix " (2)"
        artists = r.get("artists") or []
        artist  = _DISAMBIG_RE.sub("", artists[0].get("name", "")).strip() if artists else ""

        labels = r.get("labels") or []
        label  = labels[0].get("name") if labels else None
        catno  = labels[0].get("catno") if labels else None
        if catno == "none":
            catno = None

        # Primary image, else first image
        images = r.get("images") or []
        cover  = next((i["uri"] for i in images if i.get("type") == "primary"), None)
        if not cover and images:
            cover = images[0].get("uri")

        # Tracklist — skip section-header entries (position and title both present)
        tracks = []
        for t in (r.get("tracklist") or []):
            if not t.get("title"):
                continue
            tracks.append(DiscogsTrack(
                position = t.get("position", ""),
                title    = t.get("title", ""),
                duration = t.get("duration") or None,
            ))

        return DiscogsRelease(
            id        = r["id"],
            artist    = artist,
            title     = r.get("title", ""),
            year      = r.get("year"),
            label     = label,
            catno     = catno,
            cover_url = cover,
            country   = r.get("country") or None,
            genres    = r.get("genres") or [],
            styles    = r.get("styles") or [],
            tracks    = tracks,
        )

    def fetch_cover_bytes(self, url: str) -> bytes:
        """Download cover art with Discogs auth. Raises on failure."""
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Authorization": f"Discogs token={settings.discogs_token}",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.read()
