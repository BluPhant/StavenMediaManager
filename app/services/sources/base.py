"""
Abstract base for all download sources.

To add a new source (e.g. Usenet, qBittorrent, RSS):
  1. Subclass BaseSource
  2. Implement list_ready(), download(), and mark_done()
  3. Register it in syncer.py
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SourceItem:
    """A single item ready to be fetched from a remote source."""
    id: str                          # unique identifier within the source
    name: str                        # display name / destination directory name
    remote_path: str                 # full path on the remote system
    suggested_type: str              # incoming subfolder: movies, tv, audiobooks, etc.
    size_bytes: int = 0
    metadata: dict = field(default_factory=dict)  # source-specific extra data


class BaseSource(ABC):
    """All download sources implement this interface."""

    @property
    def default_category(self) -> str:
        """Category / label to apply when loading a new torrent into this source."""
        return ""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if required settings are present."""
        ...

    @abstractmethod
    def list_ready(self, exclude_ids: set | None = None) -> list[SourceItem]:
        """Return items that are ready to download (tagged, complete, within lookback).
        exclude_ids — hashes already recorded as synced; sources should skip expensive
                      per-item metadata fetches (e.g. XMLRPC file-list calls) for these."""
        ...

    @abstractmethod
    def download(self, item: SourceItem, dest_dir: str, progress_cb=None,
                 cancel_check=None) -> None:
        """Download item contents into dest_dir.
        progress_cb(pct, filename, mbps) is called periodically if provided.
        cancel_check() — if provided and returns True, abort mid-stream."""
        ...

    @abstractmethod
    def mark_done(self, item: SourceItem) -> None:
        """Called after a successful download. Rename/remove tag to prevent re-import."""
        ...
