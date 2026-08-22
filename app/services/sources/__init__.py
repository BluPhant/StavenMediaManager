"""
Source registry — returns the correct source based on configuration.

Priority: qBittorrent (if configured) > rTorrent (if configured) > None.

All callers that need "the active seedbox" should use get_active_source() rather
than instantiating RtorrentSource or QbittorrentSource directly.  This lets the
operator switch seedbox clients by flipping env vars without touching any other code.

rTorrent callers that need extract_info_hash / extract_torrent_name should still
import those utilities directly from .rtorrent (they have no rTorrent runtime dep).
"""
from __future__ import annotations

from .base import BaseSource
from .qbittorrent import QbittorrentSource
from .rtorrent import RtorrentSource


def get_active_source() -> BaseSource | None:
    """
    Return a configured source, or None if none is available.
    qBittorrent takes priority over rTorrent when both are configured.
    """
    qbt = QbittorrentSource()
    if qbt.is_configured():
        return qbt
    rt = RtorrentSource()
    if rt.is_configured():
        return rt
    return None


def get_all_sources() -> list[tuple[str, BaseSource]]:
    """
    Return all configured sources as [(name, source)] pairs.
    Used by syncer so all sources are polled on each sync run.
    """
    result: list[tuple[str, BaseSource]] = []
    qbt = QbittorrentSource()
    if qbt.is_configured():
        result.append(("qbittorrent", qbt))
    rt = RtorrentSource()
    if rt.is_configured():
        result.append(("rtorrent", rt))
    return result
