"""
rTorrent source — polls via XMLRPC, downloads via FTPS.

Compatible with usbx.me / Ultra.cc shared seedboxes:
  XMLRPC endpoint:  https://<user>.<server>.usbx.me/RPC2
  FTPS:             host 216.163.184.165 (or servername.usbx.me)  port 21

All credentials come from environment variables (see config.py).
No secrets are stored in this file.
"""
import logging
import os
import subprocess
import threading
import time
import xmlrpc.client
from urllib.parse import urlparse, urlunparse

from ...config import settings
from .base import BaseSource, SourceItem

logger = logging.getLogger(__name__)

# ── Type detection ────────────────────────────────────────────────────────────

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


# ── XMLRPC transport with timeout ────────────────────────────────────────────

class _TimeoutTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: int = 30):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = xmlrpc.client.SafeTransport.make_connection(self, host)
        conn.timeout = self._timeout
        return conn


# ── Path helpers ──────────────────────────────────────────────────────────────

def _ftp_root() -> str:
    """Absolute filesystem path that maps to the FTP root on the server."""
    root = settings.rtorrent_ftp_root
    if not root:
        root = f"/home/{settings.rtorrent_user}"
    return root.rstrip("/")


def _to_ftp_path(fs_path: str) -> str:
    """Convert absolute filesystem path → FTP-absolute path (starts with /)."""
    root = _ftp_root()
    fs_path = fs_path.rstrip("/")
    if fs_path.startswith(root):
        rel = fs_path[len(root):]
        return rel or "/"
    return "/" + fs_path.lstrip("/")


# ── lftp-based download ───────────────────────────────────────────────────────
# Python's ftplib + ssl runs ~0.9 MB/s due to GIL + per-record Python overhead.
# lftp is native C + OpenSSL — same stack as FileZilla — and matches its speed.

def _lftp_script(remote_path: str, local_path: str, is_dir: bool) -> str:
    """Build an lftp command script for a single torrent download."""
    host = settings.rtorrent_ftp_host or settings.rtorrent_ssh_host
    port = settings.rtorrent_ftp_port
    user = settings.rtorrent_user
    password = settings.rtorrent_pass
    threads = settings.rtorrent_ftp_threads

    lines = [
        "set ssl:verify-certificate no",
        "set ftp:ssl-force yes",
        "set ftp:passive-mode yes",
        f"set net:max-retries 3",
        f"set net:reconnect-interval-base 5",
        f'open -u "{user}","{password}" ftps://{host}:{port}',
    ]
    if is_dir:
        # mirror: parallel transfer of every file in the remote directory
        lines.append(
            f'mirror --parallel={threads} --no-perms --no-umask '
            f'"{remote_path}" "{local_path}"'
        )
    else:
        # pget: parallel-segment download of a single large file
        lines.append(f'pget -n {threads} "{remote_path}" -o "{local_path}"')
    lines.append("quit")
    return "\n".join(lines)



# ── rTorrent source ───────────────────────────────────────────────────────────

class RtorrentSource(BaseSource):
    """Polls rTorrent via XMLRPC; downloads via lftp (native FTPS, parallel segments)."""

    def is_configured(self) -> bool:
        return bool(
            settings.rtorrent_url
            and settings.rtorrent_user
            and (settings.rtorrent_ftp_host or settings.rtorrent_ssh_host)
        )

    def _proxy(self) -> xmlrpc.client.ServerProxy:
        url = settings.rtorrent_url
        if settings.rtorrent_user:
            parsed = urlparse(url)
            auth_netloc = f"{settings.rtorrent_user}:{settings.rtorrent_pass}@{parsed.hostname}"
            if parsed.port:
                auth_netloc += f":{parsed.port}"
            url = urlunparse(parsed._replace(netloc=auth_netloc))
        return xmlrpc.client.ServerProxy(url, transport=_TimeoutTransport(timeout=30))

    def list_ready(self) -> list[SourceItem]:
        proxy = self._proxy()
        tag = settings.rtorrent_tag.lower()
        logger.info(f"Connecting to rTorrent XMLRPC: {settings.rtorrent_url}")
        try:
            rows = proxy.d.multicall2(
                "", "main",
                "d.name=",
                "d.custom1=",
                "d.directory=",
                "d.hash=",
                "d.complete=",
                "d.size_bytes=",
                "d.is_multi_file=",
            )
            logger.info(f"rTorrent: {len(rows)} total, filtering by tag='{tag}'")
        except Exception as exc:
            logger.error(f"rTorrent XMLRPC failed: {exc}")
            raise RuntimeError(f"rTorrent XMLRPC error: {exc}") from exc

        items: list[SourceItem] = []
        for name, label, directory, hash_, complete, size, is_multi in rows:
            if not complete:
                continue
            if tag and label.lower().strip() != tag:
                continue

            remote_path = directory if is_multi else os.path.join(directory, name)

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
                metadata={"label": label, "files": file_list, "is_multi": bool(is_multi)},
            ))

        return items

    def mark_done(self, item: SourceItem) -> None:
        """No-op — import state is tracked locally in synced_items."""
        pass

    def download(self, item: SourceItem, dest_dir: str, progress_cb=None,
                 cancel_check=None) -> None:
        """Download via lftp (native FTPS, parallel) — matches FileZilla throughput."""
        remote_path = _to_ftp_path(item.remote_path)
        is_multi = item.metadata.get("is_multi", False)
        total_bytes = max(item.size_bytes, 1)
        filename = item.name

        os.makedirs(dest_dir, exist_ok=True)

        script = _lftp_script(remote_path, dest_dir, is_dir=is_multi)
        logger.info(
            f"lftp download: {item.name}  remote={remote_path}  "
            f"is_dir={is_multi}  size={total_bytes/1024/1024:.1f} MB"
        )

        proc = subprocess.Popen(
            ["lftp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        proc.stdin.write(script.encode())
        proc.stdin.close()

        t_start = time.monotonic()
        last_log = [t_start]
        last_size = [0]
        last_size_t = [t_start]

        def _drain():
            for line in proc.stdout:
                txt = line.decode(errors="replace").strip()
                if txt:
                    logger.debug(f"lftp: {txt}")

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()

        def _dir_bytes(path: str) -> int:
            total = 0
            try:
                for root, _, files in os.walk(path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
            except OSError:
                pass
            return total

        while proc.poll() is None:
            if cancel_check and cancel_check():
                proc.terminate()
                raise InterruptedError("Download cancelled")

            time.sleep(1.0)

            now = time.monotonic()
            if progress_cb and now - last_log[0] >= 2.0:
                current = _dir_bytes(dest_dir)
                dt = max(now - last_size_t[0], 0.001)
                mbps = (current - last_size[0]) / 1024 / 1024 / dt
                pct = min(int(current / total_bytes * 100), 99)
                last_size[0] = current
                last_size_t[0] = now
                last_log[0] = now

                elapsed = max(now - t_start, 0.001)
                logger.info(
                    f"lftp ↓  {filename}  "
                    f"{current/1024/1024:.1f}/{total_bytes/1024/1024:.1f} MB  "
                    f"{pct}%  {mbps:.1f} MB/s"
                )
                progress_cb(pct, filename, mbps)

        drain_thread.join(timeout=3)
        rc = proc.returncode
        if rc != 0:
            raise RuntimeError(f"lftp exited with code {rc} downloading {item.name}")

        elapsed = max(time.monotonic() - t_start, 0.001)
        avg_mbps = (total_bytes / 1024 / 1024) / elapsed
        logger.info(
            f"lftp ↓ DONE  {item.name}  avg {avg_mbps:.1f} MB/s  ({elapsed:.0f}s)"
        )
        if progress_cb:
            progress_cb(100, filename, avg_mbps)
