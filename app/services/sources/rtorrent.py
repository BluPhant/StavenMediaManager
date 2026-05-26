"""
rTorrent source — polls via XMLRPC, downloads via SFTP.

Compatible with usbx.me / Ultra.cc shared seedboxes:
  XMLRPC endpoint:  https://<user>.<server>.usbx.me/RPC2
  SFTP:             <server>.usbx.me  port 22

All credentials come from environment variables (see config.py).
No secrets are stored in this file.
"""
import logging
import os
import stat
import time
import xmlrpc.client
from urllib.parse import urlparse, urlunparse

import paramiko

from ...config import settings
from .base import BaseSource, SourceItem

logger = logging.getLogger(__name__)

# ── Type detection ────────────────────────────────────────────────────────────

# ruTorrent label (d.custom1) → incoming subfolder
LABEL_TYPE_MAP: dict[str, str] = {
    "movies":     "movies",
    "movie":      "movies",
    "films":      "movies",
    "tv":         "tv",
    "tv-shows":   "tv",
    "television": "tv",
    "shows":      "tv",
    "audiobooks": "audiobooks",
    "audiobook":  "audiobooks",
    "music":      "music",
    "ebooks":     "ebooks",
    "ebook":      "ebooks",
    "books":      "books",
    "games":      "games",
    "switch":     "switch-games",
    "pc-games":   "pc-games",
    "software":   "software",
}

# Dominant file extension → incoming subfolder (fallback when label is unrecognised)
EXT_TYPE_MAP: dict[str, str] = {
    ".mkv":  "movies",
    ".mp4":  "movies",
    ".avi":  "movies",
    ".m4v":  "movies",
    ".mp3":  "music",
    ".flac": "music",
    ".m4a":  "music",
    ".aac":  "music",
    ".m4b":  "audiobooks",
    ".epub": "ebooks",
    ".mobi": "ebooks",
    ".azw3": "ebooks",
    ".xci":  "switch-games",
    ".nsp":  "switch-games",
    ".iso":  "games",
}


def detect_type(label: str, file_paths: list[str]) -> str:
    """Determine incoming subfolder from label first, then dominant file extension."""
    if label:
        t = LABEL_TYPE_MAP.get(label.lower().strip())
        if t:
            return t

    votes: dict[str, int] = {}
    for path in file_paths:
        ext = os.path.splitext(path)[1].lower()
        t = EXT_TYPE_MAP.get(ext)
        if t:
            votes[t] = votes.get(t, 0) + 1

    return max(votes, key=votes.get) if votes else "_unsorted"


# ── rTorrent source ───────────────────────────────────────────────────────────

class RtorrentSource(BaseSource):
    """
    Polls an rTorrent instance via XMLRPC and downloads completed,
    tagged torrents via SFTP.
    """

    def is_configured(self) -> bool:
        return bool(
            settings.rtorrent_url
            and settings.rtorrent_ssh_host
            and settings.rtorrent_ssh_user
        )

    # ── XMLRPC ───────────────────────────────────────────────────────────────

    def _proxy(self) -> xmlrpc.client.ServerProxy:
        """Build an XMLRPC proxy with credentials injected into the URL."""
        url = settings.rtorrent_url
        if settings.rtorrent_user:
            parsed = urlparse(url)
            auth_netloc = f"{settings.rtorrent_user}:{settings.rtorrent_pass}@{parsed.hostname}"
            if parsed.port:
                auth_netloc += f":{parsed.port}"
            url = urlunparse(parsed._replace(netloc=auth_netloc))
        return xmlrpc.client.ServerProxy(url)

    def list_ready(self) -> list[SourceItem]:
        proxy = self._proxy()
        cutoff = int(time.time()) - settings.rtorrent_lookback_hours * 3600
        tag = settings.rtorrent_tag.lower()

        # Fetch all torrents with relevant fields
        # d.custom1 = ruTorrent label; d.completion_on = unix timestamp of completion
        try:
            rows = proxy.d.multicall2(
                "", "main",
                "d.name=",
                "d.custom1=",
                "d.directory=",
                "d.completion_on=",
                "d.hash=",
                "d.complete=",
                "d.size_bytes=",
                "d.is_multi_file=",
            )
        except Exception as exc:
            raise RuntimeError(f"rTorrent XMLRPC error: {exc}") from exc

        items: list[SourceItem] = []
        for name, label, directory, finished_at, hash_, complete, size, is_multi in rows:
            if not complete:
                continue
            if tag and label.lower().strip() != tag:
                continue
            if finished_at and int(finished_at) < cutoff:
                continue

            # Multi-file torrents: directory is the torrent root folder
            # Single-file:        directory is the containing folder
            remote_path = directory if is_multi else os.path.join(directory, name)

            # Get individual file paths for type detection
            try:
                file_rows = proxy.f.multicall(hash_, "", "f.path=")
                file_list = [r[0] for r in file_rows]
            except Exception:
                file_list = [name]

            items.append(SourceItem(
                id=hash_,
                name=name,
                remote_path=remote_path,
                suggested_type=detect_type(label, file_list),
                size_bytes=int(size),
                metadata={"label": label, "files": file_list},
            ))

        return items

    def mark_done(self, item: SourceItem) -> None:
        """Append '-imported' to the label so this torrent won't be picked up again."""
        try:
            proxy = self._proxy()
            new_label = f"{item.metadata.get('label', settings.rtorrent_tag)}-imported"
            proxy.d.custom1.set(item.id, new_label)
        except Exception as exc:
            logger.warning(f"Could not update label for {item.name}: {exc}")

    # ── SFTP download ─────────────────────────────────────────────────────────

    def _ssh_client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = dict(
            hostname=settings.rtorrent_ssh_host,
            port=settings.rtorrent_ssh_port,
            username=settings.rtorrent_ssh_user,
        )
        key_path = settings.rtorrent_ssh_key_path
        if key_path and os.path.exists(key_path):
            connect_kwargs["key_filename"] = key_path
            connect_kwargs["look_for_keys"] = False
        elif settings.rtorrent_ssh_pass:
            connect_kwargs["password"] = settings.rtorrent_ssh_pass
            connect_kwargs["look_for_keys"] = False

        client.connect(**connect_kwargs)
        return client

    def download(self, item: SourceItem, dest_dir: str, progress_cb=None) -> None:
        ssh = self._ssh_client()
        try:
            sftp = ssh.open_sftp()
            try:
                _sftp_get(sftp, item.remote_path, dest_dir, progress_cb)
            finally:
                sftp.close()
        finally:
            ssh.close()


# ── SFTP helpers ──────────────────────────────────────────────────────────────

def _sftp_get(
    sftp: paramiko.SFTPClient,
    remote: str,
    local_dir: str,
    progress_cb=None,
) -> None:
    """Recursively download remote path into local_dir."""
    os.makedirs(local_dir, exist_ok=True)

    try:
        mode = sftp.stat(remote).st_mode
    except FileNotFoundError as exc:
        raise RuntimeError(f"Remote path not found: {remote}") from exc

    if stat.S_ISDIR(mode):
        for entry in sftp.listdir_attr(remote):
            _sftp_get(
                sftp,
                f"{remote}/{entry.filename}",
                os.path.join(local_dir, entry.filename),
                progress_cb,
            )
    else:
        filename = os.path.basename(remote)
        local_path = os.path.join(local_dir, filename)
        total = max(sftp.stat(remote).st_size, 1)
        transferred: list[int] = [0]

        def _cb(done: int, _total: int) -> None:
            transferred[0] = done
            if progress_cb:
                progress_cb(int(done / total * 100), filename)

        sftp.get(remote, local_path, callback=_cb)
