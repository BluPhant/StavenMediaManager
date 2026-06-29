"""
rTorrent source — polls via XMLRPC, downloads via curl (FTPS).

Compatible with usbx.me / Ultra.cc shared seedboxes:
  XMLRPC endpoint:  https://<user>.<server>.usbx.me/RPC2
  FTPS:             host 216.163.184.165  port 21

FTP path mapping:
  The FTP server is chrooted to the user's home dir (e.g. /home/emuhack).
  rTorrent stores files under e.g. /home/emuhack/files/<torrent-name>/
  → FTP path = /files/<torrent-name>/filename
  Set RTORRENT_FTP_ROOT to the home dir (default: /home/<user>).

Download strategy:
  curl (native C + OpenSSL) is used instead of Python ftplib to avoid the
  GIL + per-TLS-record Python overhead that limits ftplib to ~0.9 MB/s.
  Tested throughput: 26 MB/s single connection, 34 MB/s with 4 parallel.
  - Single-file torrent: N_THREADS parallel range-segment downloads, merged.
  - Multi-file torrent:  up to N_THREADS concurrent per-file curl processes.

All credentials come from environment variables (see config.py).
No secrets are stored in this file.
"""
import hashlib
import logging
import os
import re
import subprocess
import threading
import time
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse, urlunparse

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


_TV_RE = re.compile(r"[.\s]S\d{2}(?:E\d{2})?[.\s]|[.\s](?:Season|Series)[.\s]?\d", re.IGNORECASE)
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts"}
_SAMPLE_RE = re.compile(r"(?:^|[/\\])sample[/\\]|(?:^|[/\\])sample\.", re.IGNORECASE)


def _filter_movie_files(file_list: list[str]) -> list[str]:
    """For movie torrents: keep the main video file + non-video extras (nfo, srt).
    Exclude sample videos and 'Screens' directories."""
    videos = []
    others = []
    for f in file_list:
        ext = os.path.splitext(f)[1].lower()
        basename = os.path.basename(f).lower()
        # Skip anything in a Sample/ or Screens/ subdirectory
        if _SAMPLE_RE.search(f) or f.lower().startswith("screens/") or "/screens/" in f.lower():
            continue
        if ext in _VIDEO_EXTS:
            videos.append(f)
        else:
            others.append(f)
    # Keep only the largest video (by name heuristic: the main feature, not a sample)
    if len(videos) > 1:
        # If any video has "sample" in name, exclude it
        non_sample = [v for v in videos if "sample" not in os.path.basename(v).lower()]
        videos = non_sample if non_sample else videos[:1]
    return videos + others


def detect_type(label: str, file_paths: list[str], name: str = "") -> str:
    if label:
        t = LABEL_TYPE_MAP.get(label.lower().strip())
        if t:
            return t
    # Check torrent name for TV season/episode markers before extension voting
    if name and _TV_RE.search(name):
        return "tv"
    votes: dict[str, int] = {}
    for path in file_paths:
        ext = os.path.splitext(path)[1].lower()
        t = EXT_TYPE_MAP.get(ext)
        if t:
            votes[t] = votes.get(t, 0) + 1
    return max(votes, key=votes.get) if votes else "_unsorted"


# ── Torrent info-hash extraction ─────────────────────────────────────────────

def extract_info_hash(torrent_bytes: bytes) -> str:
    """
    Return the uppercase hex info-hash (SHA-1 of the bencoded 'info' dict)
    from a raw .torrent file.  Pure stdlib — no external deps.
    """
    marker = b"4:info"
    idx = torrent_bytes.find(marker)
    if idx == -1:
        raise ValueError("No 'info' key found in torrent data")
    info_start = idx + len(marker)
    info_end = _bencode_end(torrent_bytes, info_start)
    return hashlib.sha1(torrent_bytes[info_start:info_end]).hexdigest().upper()


def extract_torrent_name(torrent_bytes: bytes) -> str:
    """Extract the directory/file name from a .torrent file (info.name field)."""
    info_marker = b"4:info"
    idx = torrent_bytes.find(info_marker)
    if idx == -1:
        raise ValueError("No 'info' key found in torrent data")
    info_start = idx + len(info_marker)
    info_end = _bencode_end(torrent_bytes, info_start)
    info_bytes = torrent_bytes[info_start:info_end]
    name_marker = b"4:name"
    name_idx = info_bytes.find(name_marker)
    if name_idx == -1:
        raise ValueError("No 'name' key in info dict")
    val_start = name_idx + len(name_marker)
    colon = info_bytes.index(b":", val_start)
    length = int(info_bytes[val_start:colon])
    return info_bytes[colon + 1: colon + 1 + length].decode("utf-8", errors="replace")


def _bencode_end(data: bytes, pos: int) -> int:
    """Return the index one past the end of the bencoded value starting at pos."""
    c = data[pos:pos + 1]
    if c == b"d":
        pos += 1
        while data[pos:pos + 1] != b"e":
            pos = _bencode_end(data, pos)   # key
            pos = _bencode_end(data, pos)   # value
        return pos + 1
    elif c == b"l":
        pos += 1
        while data[pos:pos + 1] != b"e":
            pos = _bencode_end(data, pos)
        return pos + 1
    elif c == b"i":
        return data.index(b"e", pos + 1) + 1
    elif c.isdigit():
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        return colon + 1 + length
    else:
        raise ValueError(f"Unknown bencode type {c!r} at offset {pos}")


# ── XMLRPC transport with timeout ─────────────────────────────────────────────

class _TimeoutTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: int = 30):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = xmlrpc.client.SafeTransport.make_connection(self, host)
        conn.timeout = self._timeout
        return conn


# ── FTP path helpers ──────────────────────────────────────────────────────────

def _ftp_root() -> str:
    """Filesystem path on the server that corresponds to the FTP root (/)."""
    root = settings.rtorrent_ftp_root
    if not root:
        root = f"/home/{settings.rtorrent_user}"
    return root.rstrip("/")


def _to_ftp_path(fs_path: str) -> str:
    """Convert an absolute filesystem path on the server to an FTP-absolute path."""
    root = _ftp_root()
    fs_path = fs_path.rstrip("/")
    if fs_path.startswith(root):
        rel = fs_path[len(root):]
        return rel or "/"
    return "/" + fs_path.lstrip("/")


def _ftp_url(ftp_path: str) -> str:
    """Build a full ftp:// URL with URL-encoded path components."""
    host = settings.rtorrent_ftp_host or settings.rtorrent_ssh_host
    port = settings.rtorrent_ftp_port
    # Encode each path segment separately (preserve slashes)
    encoded = "/".join(quote(seg, safe="") for seg in ftp_path.split("/"))
    return f"ftp://{host}:{port}{encoded}"


def _curl_base() -> list[str]:
    """Base curl arguments shared by all transfers."""
    return [
        "curl",
        "--ftp-ssl",           # FTPS (AUTH TLS on control + data channel)
        "-k",                  # skip cert verification (seedbox self-signed)
        "--user", f"{settings.rtorrent_user}:{settings.rtorrent_pass}",
        "--ftp-pasv",          # passive mode
        "--retry", "3",
        "--retry-delay", "5",
        "--silent",
        "--show-error",
    ]


# ── curl download helpers ─────────────────────────────────────────────────────

def _curl_file_size(ftp_path: str) -> int:
    """Return the remote file size in bytes via curl HEAD-equivalent."""
    try:
        result = subprocess.run(
            _curl_base() + ["--head", "--write-out", "%{size_download}", _ftp_url(ftp_path)],
            capture_output=True, text=True, timeout=30,
        )
        # curl --head on FTP uses SIZE command; size is in stdout or Content-Length
        # Parse from the header output
        for line in result.stdout.splitlines():
            if line.lower().startswith("content-length:"):
                return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return 0


def _curl_download_segment(ftp_path: str, local_path: str,
                            byte_range: str | None = None) -> None:
    """Download one file (or range) via curl. Raises on failure."""
    cmd = _curl_base() + ["--output", local_path, _ftp_url(ftp_path)]
    if byte_range:
        cmd += ["--range", byte_range]
    result = subprocess.run(cmd, capture_output=True, timeout=7200)
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"curl failed ({result.returncode}): {err}")


def _download_single_file(ftp_path: str, local_path: str,
                           size_bytes: int, threads: int,
                           cancel_check=None) -> None:
    """Download one large file using N parallel range-segment curl processes."""
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    filename = os.path.basename(local_path)
    t_start = time.monotonic()

    if size_bytes <= 0 or threads <= 1:
        # Single connection fallback
        logger.info(f"curl ↓ single  {filename}  ({size_bytes/1024/1024:.1f} MB)")
        if cancel_check and cancel_check():
            raise InterruptedError("Download cancelled")
        _curl_download_segment(ftp_path, local_path)
        return

    # Split into N segments
    seg_size = size_bytes // threads
    segments = []
    for i in range(threads):
        start = i * seg_size
        end = (start + seg_size - 1) if i < threads - 1 else (size_bytes - 1)
        seg_path = f"{local_path}.part{i}"
        segments.append((f"{start}-{end}", seg_path))

    logger.info(
        f"curl ↓ start  {filename}  "
        f"{size_bytes/1024/1024:.1f} MB  {threads} segments"
    )

    errors = []
    procs: list[subprocess.Popen] = []

    def _run_seg(byte_range: str, seg_path: str) -> None:
        cmd = _curl_base() + ["--output", seg_path, "--range", byte_range,
                               _ftp_url(ftp_path)]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        procs.append(p)
        _, stderr = p.communicate()
        procs.remove(p)
        if p.returncode != 0:
            errors.append(stderr.decode(errors="replace").strip())

    threads_list = []
    for byte_range, seg_path in segments:
        t = threading.Thread(target=_run_seg, args=(byte_range, seg_path), daemon=True)
        t.start()
        threads_list.append(t)

    # Monitor progress + cancellation while segments download
    while any(t.is_alive() for t in threads_list):
        if cancel_check and cancel_check():
            for p in list(procs):
                try:
                    p.terminate()
                except Exception:
                    pass
            raise InterruptedError("Download cancelled")
        time.sleep(1.0)

    for t in threads_list:
        t.join()

    if errors:
        raise RuntimeError(f"Segment download failed: {errors[0]}")

    # Concatenate segments in order
    logger.info(f"curl ↓ merge   {filename}")
    with open(local_path, "wb") as out:
        for _, seg_path in segments:
            with open(seg_path, "rb") as seg:
                while True:
                    chunk = seg.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            os.remove(seg_path)

    elapsed = max(time.monotonic() - t_start, 0.001)
    avg_mbps = (size_bytes / 1024 / 1024) / elapsed
    logger.info(f"curl ↓ done    {filename}  avg {avg_mbps:.1f} MB/s  ({elapsed:.0f}s)")


# ── rTorrent source ───────────────────────────────────────────────────────────

class RtorrentSource(BaseSource):
    """Polls rTorrent via XMLRPC; downloads via curl (FTPS, parallel segments)."""

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
            auth_netloc = (
                f"{settings.rtorrent_user}:{settings.rtorrent_pass}"
                f"@{parsed.hostname}"
            )
            if parsed.port:
                auth_netloc += f":{parsed.port}"
            url = urlunparse(parsed._replace(netloc=auth_netloc))
        return xmlrpc.client.ServerProxy(url, transport=_TimeoutTransport(timeout=30))

    def load_torrent(self, torrent_bytes: bytes, label: str = "") -> None:
        """
        Load a .torrent file into rTorrent via XMLRPC load.raw_start.
        Sets the ruTorrent label (d.custom1) to `label` if provided.
        Raises RuntimeError on failure.
        """
        proxy = self._proxy()
        binary = xmlrpc.client.Binary(torrent_bytes)
        tag = label or settings.rtorrent_tag

        try:
            # load.raw_start: load + immediately start the torrent.
            # Extra positional args are rTorrent commands run at load time.
            proxy.load.raw_start("", binary, f"d.custom1.set={tag}")
            logger.info(f"Loaded torrent into rTorrent with label={tag!r}")
        except Exception as exc:
            raise RuntimeError(f"rTorrent load.raw_start failed: {exc}") from exc

    def get_item_by_hash(self, hash_: str) -> "SourceItem":
        """
        Return a SourceItem for a specific torrent hash, regardless of label.
        Raises ValueError if the torrent is not complete.
        """
        proxy  = self._proxy()
        hash_  = hash_.upper()
        name      = proxy.d.name(hash_)
        directory = proxy.d.directory(hash_)
        is_multi  = bool(proxy.d.is_multi_file(hash_))
        size      = int(proxy.d.size_bytes(hash_))
        label     = proxy.d.custom1(hash_)
        complete  = bool(proxy.d.complete(hash_))

        if not complete:
            done = int(proxy.d.completed_bytes(hash_))
            pct  = round(done / max(size, 1) * 100, 1)
            raise ValueError(f"Torrent is not complete ({pct}%)")

        try:
            file_rows = proxy.f.multicall(hash_, "", "f.path=")
            file_list = [r[0] for r in file_rows]
        except Exception:
            file_list = [name]

        remote_path = directory if is_multi else os.path.join(directory, name)
        return SourceItem(
            id=hash_,
            name=name,
            remote_path=remote_path,
            suggested_type=detect_type(label, file_list, name),
            size_bytes=size,
            metadata={"label": label, "files": file_list, "is_multi": bool(is_multi)},
        )

    def list_all_brief(self) -> dict[str, dict]:
        """
        Return every torrent currently in rTorrent as {HASH: {name, label, pct}}.
        Fast — single d.multicall2 call.
        """
        proxy = self._proxy()
        try:
            rows = proxy.d.multicall2("", "main",
                "d.hash=", "d.name=", "d.custom1=",
                "d.completed_bytes=", "d.size_bytes=")
        except Exception as exc:
            raise RuntimeError(f"rTorrent d.multicall2 failed: {exc}") from exc

        result = {}
        for row in rows:
            hash_, name, label, done, total = row
            pct = round(done / max(total, 1) * 100, 1)
            result[hash_.upper()] = {"name": name, "label": label, "pct": pct, "size_bytes": int(total)}
        return result

    def check_new_completions(self, label: str, exclude_ids: set[str]) -> list[tuple[str, str]]:
        """
        Lightweight check: return [(hash, name)] of completed torrents with the
        given label that are NOT in exclude_ids.  Single XML-RPC call, no file
        listing or type detection — much cheaper than list_ready().
        """
        proxy = self._proxy()
        rows = proxy.d.multicall2("", "main",
            "d.hash=", "d.name=", "d.complete=", "d.custom1=")
        result = []
        for hash_, name, complete, lbl in rows:
            h = hash_.upper()
            if lbl.lower() == label.lower() and complete == 1 and h not in exclude_ids:
                result.append((h, name))
        return result

    def stop_torrent(self, hash_: str) -> None:
        """Stop (pause) a torrent by hash via XMLRPC d.stop."""
        proxy = self._proxy()
        try:
            proxy.d.stop(hash_)
            logger.info(f"Stopped torrent {hash_}")
        except Exception as exc:
            raise RuntimeError(f"rTorrent d.stop failed: {exc}") from exc

    def list_active(self) -> list[dict]:
        """
        Return torrents that are currently in-progress on the seedbox
        (tagged with RTORRENT_TAG but not yet complete).
        Each dict has: hash, name, label, size_bytes, bytes_done, pct,
                       down_rate (bytes/s), up_rate (bytes/s), is_active.
        """
        proxy = self._proxy()
        tag = settings.rtorrent_tag.lower()
        try:
            rows = proxy.d.multicall2(
                "", "main",
                "d.name=",
                "d.custom1=",
                "d.hash=",
                "d.complete=",
                "d.size_bytes=",
                "d.bytes_done=",
                "d.down.rate=",
                "d.up.rate=",
                "d.is_active=",
            )
        except Exception as exc:
            raise RuntimeError(f"rTorrent XMLRPC error: {exc}") from exc

        active = []
        for name, label, hash_, complete, size, done, down_rate, up_rate, is_active in rows:
            if complete:
                continue
            if tag and label.lower().strip() != tag:
                continue
            size = int(size) or 1
            done = int(done)
            pct = round(done / size * 100, 1)
            active.append({
                "hash":       hash_,
                "name":       name,
                "label":      label,
                "size_bytes": size,
                "bytes_done": done,
                "pct":        pct,
                "down_rate":  int(down_rate),
                "up_rate":    int(up_rate),
                "is_active":  bool(is_active),
            })
        return active

    def list_ready(self, exclude_ids: set | None = None) -> list[SourceItem]:
        proxy = self._proxy()
        tag = settings.rtorrent_tag.lower()
        exclude_ids = exclude_ids or set()

        logger.info(f"Connecting to rTorrent XMLRPC: {settings.rtorrent_url}")
        t0 = time.monotonic()
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
        except Exception as exc:
            logger.error(f"rTorrent XMLRPC failed: {exc}")
            raise RuntimeError(f"rTorrent XMLRPC error: {exc}") from exc

        t_bulk = time.monotonic() - t0
        logger.info(
            f"rTorrent d.multicall2: {len(rows)} total torrents in {t_bulk:.2f}s, "
            f"filtering by tag='{tag}', {len(exclude_ids)} already-synced IDs to skip"
        )

        items: list[SourceItem] = []
        skipped = 0
        file_calls = 0
        t_file_calls = 0.0

        for name, label, directory, hash_, complete, size, is_multi in rows:
            if not complete:
                continue
            if tag and label.lower().strip() != tag:
                continue

            # Skip expensive f.multicall for items we already have in the DB
            if hash_ in exclude_ids:
                skipped += 1
                continue

            remote_path = directory if is_multi else os.path.join(directory, name)

            t_f = time.monotonic()
            try:
                file_rows = proxy.f.multicall(hash_, "", "f.path=")
                file_list = [r[0] for r in file_rows]
            except Exception:
                file_list = [name]
            t_file_calls += time.monotonic() - t_f
            file_calls += 1

            items.append(SourceItem(
                id=hash_,
                name=name,
                remote_path=remote_path,
                suggested_type=detect_type(label, file_list, name),
                size_bytes=int(size),
                metadata={"label": label, "files": file_list, "is_multi": bool(is_multi)},
            ))

        logger.info(
            f"list_ready: {len(items)} new item(s), {skipped} skipped (already synced), "
            f"{file_calls} f.multicall(s) in {t_file_calls:.2f}s  "
            f"[total {time.monotonic() - t0:.2f}s]"
        )
        return items

    def mark_done(self, item: SourceItem) -> None:
        """No-op — import state is tracked locally in synced_items."""
        pass

    def download(self, item: SourceItem, dest_dir: str, progress_cb=None,
                 cancel_check=None) -> None:
        """Download via curl (FTPS). Single files use parallel segments; multi-file
        uses parallel per-file connections up to RTORRENT_FTP_THREADS."""
        ftp_dir = _to_ftp_path(item.remote_path)
        is_multi = item.metadata.get("is_multi", False)
        total_bytes = max(item.size_bytes, 1)
        threads = settings.rtorrent_ftp_threads
        name = item.name

        os.makedirs(dest_dir, exist_ok=True)

        logger.info(
            f"curl download: {name}  ftp_path={ftp_dir}  "
            f"is_multi={is_multi}  size={total_bytes/1024/1024:.1f} MB"
        )

        t_start = time.monotonic()
        last_size = [0]
        last_t = [t_start]

        def _report_progress() -> None:
            if not progress_cb:
                return
            try:
                current = sum(
                    os.path.getsize(os.path.join(r, f))
                    for r, _, files in os.walk(dest_dir)
                    for f in files
                    if not re.search(r"\.part\d+$", f)
                )
            except OSError:
                current = 0
            now = time.monotonic()
            dt = max(now - last_t[0], 0.001)
            mbps = (current - last_size[0]) / 1024 / 1024 / dt
            pct = min(int(current / total_bytes * 100), 99)
            last_size[0] = current
            last_t[0] = now
            elapsed = max(now - t_start, 0.001)
            logger.info(
                f"curl ↓  {name}  "
                f"{current/1024/1024:.1f}/{total_bytes/1024/1024:.1f} MB  "
                f"{pct}%  {mbps:.1f} MB/s"
            )
            progress_cb(pct, name, mbps)

        if not is_multi:
            # Single file — parallel segment download
            filename = os.path.basename(item.remote_path)
            local_path = os.path.join(dest_dir, filename)

            # Progress reporter while download runs
            done_event = threading.Event()

            def _progress_loop():
                while not done_event.wait(timeout=2.0):
                    _report_progress()

            prog_thread = threading.Thread(target=_progress_loop, daemon=True)
            prog_thread.start()
            try:
                _download_single_file(
                    ftp_dir, local_path, total_bytes, threads,
                    cancel_check=cancel_check,
                )
            finally:
                done_event.set()
                prog_thread.join(timeout=3)
        else:
            # Multi-file — one curl per file, up to `threads` concurrent
            file_list = item.metadata.get("files", [])
            if not file_list:
                raise RuntimeError(f"No file list available for multi-file torrent {name}")

            # For movies: skip sample files and non-essential extras
            if item.suggested_type == "movies":
                file_list = _filter_movie_files(file_list)

            transfers = [
                (f"{ftp_dir}/{f}".replace("//", "/"),
                 os.path.join(dest_dir, f))
                for f in file_list
            ]

            logger.info(
                f"curl ↓ multi  {name}  "
                f"{len(transfers)} file(s)  {threads} concurrent"
            )

            completed = [0]
            active_procs: list[subprocess.Popen] = []
            active_procs_lock = threading.Lock()

            def _dl_one(ftp_path: str, local_path: str) -> None:
                os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
                cmd = _curl_base() + ["--output", local_path, _ftp_url(ftp_path)]
                p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                with active_procs_lock:
                    active_procs.append(p)
                _, stderr = p.communicate()
                with active_procs_lock:
                    if p in active_procs:
                        active_procs.remove(p)
                if p.returncode != 0:
                    err = stderr.decode(errors="replace").strip()
                    raise RuntimeError(f"curl failed ({p.returncode}): {err}")
                completed[0] += 1

            def _kill_all_procs():
                with active_procs_lock:
                    for p in list(active_procs):
                        try:
                            p.terminate()
                        except Exception:
                            pass

            # Background progress thread — same pattern as single-file path
            done_event = threading.Event()

            def _progress_loop():
                while not done_event.wait(timeout=2.0):
                    _report_progress()

            prog_thread = threading.Thread(target=_progress_loop, daemon=True)
            prog_thread.start()
            try:
                with ThreadPoolExecutor(max_workers=threads) as pool:
                    futures = {
                        pool.submit(_dl_one, ftp, loc): (ftp, loc)
                        for ftp, loc in transfers
                    }
                    for future in as_completed(futures):
                        if cancel_check and cancel_check():
                            _kill_all_procs()
                            pool.shutdown(wait=False, cancel_futures=True)
                            raise InterruptedError("Download cancelled")
                        future.result()  # re-raises any exception
            except InterruptedError:
                _kill_all_procs()
                raise
            finally:
                done_event.set()
                prog_thread.join(timeout=3)

        elapsed = max(time.monotonic() - t_start, 0.001)
        avg_mbps = (total_bytes / 1024 / 1024) / elapsed
        logger.info(f"curl ↓ done  {name}  avg {avg_mbps:.1f} MB/s  ({elapsed:.0f}s)")
        if progress_cb:
            progress_cb(100, name, avg_mbps)
