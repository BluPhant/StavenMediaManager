"""
rTorrent source — polls via XMLRPC, downloads via FTPS.

Compatible with usbx.me / Ultra.cc shared seedboxes:
  XMLRPC endpoint:  https://<user>.<server>.usbx.me/RPC2
  FTPS:             host 216.163.184.165 (or servername.usbx.me)  port 21

All credentials come from environment variables (see config.py).
No secrets are stored in this file.
"""
import ftplib
import logging
import os
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# ── FTPS helpers ──────────────────────────────────────────────────────────────

def _ftp_root() -> str:
    """FTP root path on the server — paths below this are FTP-relative."""
    root = settings.rtorrent_ftp_root
    if not root:
        root = f"/home/{settings.rtorrent_user}"
    return root.rstrip("/")


def _to_ftp_path(fs_path: str) -> str:
    """Convert absolute filesystem path to FTP-relative path."""
    root = _ftp_root()
    fs_path = fs_path.rstrip("/")
    if fs_path.startswith(root):
        rel = fs_path[len(root):]
        return rel.lstrip("/") or "."
    # Fallback: strip leading slash
    return fs_path.lstrip("/")


def _ftps_connect() -> ftplib.FTP_TLS:
    """Open an authenticated FTPS connection."""
    ftp = ftplib.FTP_TLS()
    ftp.connect(
        settings.rtorrent_ftp_host or settings.rtorrent_ssh_host,
        settings.rtorrent_ftp_port,
        timeout=30,
    )
    ftp.login(settings.rtorrent_user, settings.rtorrent_pass)
    ftp.prot_p()   # encrypt the data channel
    ftp.set_pasv(True)
    return ftp


def _download_one(ftp_path: str, local_path: str, progress_cb=None) -> None:
    """Download a single file via a fresh FTPS connection."""
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    ftp = _ftps_connect()
    try:
        try:
            size = ftp.size(ftp_path) or 1
        except Exception:
            size = 1
        transferred = [0]
        filename = os.path.basename(local_path)

        with open(local_path, "wb") as f:
            def _cb(chunk: bytes) -> None:
                f.write(chunk)
                transferred[0] += len(chunk)
                if progress_cb:
                    progress_cb(int(transferred[0] / size * 100), filename)

            ftp.retrbinary(f"RETR {ftp_path}", _cb, blocksize=262144)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def _list_ftp_files(ftp_dir: str) -> list[str]:
    """Return all file paths (relative to ftp_dir) under an FTP directory."""
    ftp = _ftps_connect()
    results: list[str] = []
    try:
        _walk_ftp(ftp, ftp_dir, "", results)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return results


def _walk_ftp(ftp: ftplib.FTP_TLS, base: str, rel: str, out: list[str]) -> None:
    path = f"{base}/{rel}".rstrip("/") if rel else base
    entries: list[str] = []
    try:
        ftp.retrlines(f"NLST {path}", entries.append)
    except ftplib.error_perm:
        return
    for entry in entries:
        name = entry.split("/")[-1]
        child_rel = f"{rel}/{name}".lstrip("/") if rel else name
        child_path = f"{base}/{child_rel}"
        try:
            # Try to list as directory
            sub: list[str] = []
            ftp.retrlines(f"NLST {child_path}/", sub.append)
            if sub:
                _walk_ftp(ftp, base, child_rel, out)
                continue
        except Exception:
            pass
        out.append(child_rel)


# ── rTorrent source ───────────────────────────────────────────────────────────

class RtorrentSource(BaseSource):
    """Polls rTorrent via XMLRPC and downloads via FTPS (4 parallel connections)."""

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

    def download(self, item: SourceItem, dest_dir: str, progress_cb=None) -> None:
        """Download via FTPS using up to RTORRENT_FTP_THREADS parallel connections."""
        ftp_dir = _to_ftp_path(item.remote_path)
        is_multi = item.metadata.get("is_multi", False)

        if is_multi:
            # Build (ftp_path, local_path) pairs from file list
            file_list = item.metadata.get("files", [])
            if not file_list:
                file_list = _list_ftp_files(ftp_dir)
            transfers = [
                (f"{ftp_dir}/{f}".replace("//", "/"), os.path.join(dest_dir, f))
                for f in file_list
            ]
        else:
            filename = os.path.basename(item.remote_path)
            transfers = [(ftp_dir, os.path.join(dest_dir, filename))]

        total_files = len(transfers)
        completed = [0]

        def _file_progress(pct: int, filename: str) -> None:
            if progress_cb:
                overall = int((completed[0] + pct / 100) / total_files * 100)
                progress_cb(overall, filename)

        def _download_task(args: tuple) -> None:
            ftp_path, local_path = args
            _download_one(ftp_path, local_path, _file_progress)
            completed[0] += 1

        threads = min(settings.rtorrent_ftp_threads, total_files)
        logger.info(f"Downloading {item.name}: {total_files} file(s), {threads} thread(s)")

        with ThreadPoolExecutor(max_workers=threads) as pool:
            futures = {pool.submit(_download_task, t): t for t in transfers}
            for future in as_completed(futures):
                future.result()  # re-raises any exception
