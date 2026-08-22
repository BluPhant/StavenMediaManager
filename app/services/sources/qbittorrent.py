"""
qBittorrent source — polls via Web API (HTTP), downloads via curl (SFTP).

Compatible with Ultra.cc shared seedboxes running qBittorrent:
  Web UI / API:  https://<user>.<server>.usbx.me/qbittorrent
  SFTP:          <server>.usbx.me  port 22

Category mapping:
  qBittorrent categories replace rTorrent labels. Set QBITTORRENT_CATEGORY
  to the category on torrents you want auto-imported (e.g. "pEaNuT").

Download strategy:
  curl SFTP, same parallel-segment approach as the rTorrent FTPS path.
  Set QBITTORRENT_THREADS to control concurrency (default 4).

All credentials come from environment variables (see config.py).
No secrets are stored in this file.
"""
import logging
import os
import re
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...config import settings
from .base import BaseSource, SourceItem
from .rtorrent import _filter_movie_files, detect_type

logger = logging.getLogger(__name__)

# Torrent states that indicate the download is fully complete
_COMPLETE_STATES = frozenset({
    "uploading", "stalledUP", "checkingUP", "pausedUP", "queuedUP",
    "forcedUP", "stoppedUP",   # stoppedUP = qBittorrent v5.x name for pausedUP
})


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── qBittorrent HTTP client ───────────────────────────────────────────────────

class _QbtClient:
    """Minimal authenticated HTTP client for the qBittorrent Web API."""

    def __init__(self):
        self._sid: str | None = None

    def _base(self) -> str:
        return settings.qbittorrent_url.rstrip("/")

    def _login(self) -> None:
        data = urllib.parse.urlencode({
            "username": settings.qbittorrent_user,
            "password": settings.qbittorrent_pass,
        }).encode()
        req = urllib.request.Request(
            f"{self._base()}/api/v2/auth/login",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=15) as resp:
            body = resp.read().decode().strip()
            if body != "Ok.":
                raise RuntimeError(f"qBittorrent login failed: {body!r}")
            for part in resp.headers.get("Set-Cookie", "").split(";"):
                part = part.strip()
                if part.startswith("SID="):
                    self._sid = part[4:]
                    return
        raise RuntimeError("qBittorrent login: no SID cookie in response")

    def _headers(self) -> dict:
        if not self._sid:
            self._login()
        return {"Cookie": f"SID={self._sid}", "Referer": self._base()}

    def get(self, path: str, params: dict | None = None, _retry: bool = True) -> bytes:
        url = f"{self._base()}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 403 and _retry:
                self._sid = None
                return self.get(path, params, _retry=False)
            raise

    def post(self, path: str, data: dict | None = None, _retry: bool = True) -> bytes:
        payload = urllib.parse.urlencode(data or {}).encode()
        req = urllib.request.Request(
            f"{self._base()}{path}",
            data=payload,
            headers={**self._headers(),
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 403 and _retry:
                self._sid = None
                return self.post(path, data, _retry=False)
            raise

    def post_multipart(self, path: str, fields: dict,
                       files: dict[str, tuple[str, bytes]]) -> bytes:
        """Multipart POST — used for adding .torrent files."""
        boundary = "----StavenBoundary7MA4YWxkTrZu0gW"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
                .encode()
            )
        for name, (filename, content) in files.items():
            header = (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: application/x-bittorrent\r\n\r\n"
            ).encode()
            parts.append(header + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            f"{self._base()}{path}",
            data=body,
            headers={**self._headers(),
                     "Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=30) as resp:
            return resp.read()


# ── SFTP helpers (curl) ───────────────────────────────────────────────────────

def _sftp_url(remote_path: str) -> str:
    host = settings.qbittorrent_ssh_host
    port = settings.qbittorrent_ssh_port
    encoded = "/".join(urllib.parse.quote(seg, safe="") for seg in remote_path.split("/"))
    return f"sftp://{host}:{port}{encoded}"


def _curl_sftp_base() -> list[str]:
    cmd = ["curl", "-k", "--silent", "--show-error", "--retry", "3", "--retry-delay", "5"]
    key = settings.qbittorrent_ssh_key_path
    if key and os.path.exists(key):
        cmd += ["--key", key]
    user = settings.qbittorrent_ssh_user or settings.qbittorrent_user
    cmd += ["--user", f"{user}:{settings.qbittorrent_ssh_pass}"]
    return cmd


def _download_single_sftp(remote_path: str, local_path: str,
                           size_bytes: int, threads: int,
                           cancel_check=None) -> None:
    """Download one file via SFTP using N parallel range-segment curl processes."""
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    filename = os.path.basename(local_path)
    url = _sftp_url(remote_path)
    t_start = time.monotonic()

    if size_bytes <= 0 or threads <= 1:
        logger.info(f"sftp ↓ single  {filename}")
        if cancel_check and cancel_check():
            raise InterruptedError("Download cancelled")
        result = subprocess.run(
            _curl_sftp_base() + ["--output", local_path, url],
            capture_output=True, timeout=7200,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"curl sftp failed ({result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        return

    seg_size = size_bytes // threads
    segments = []
    for i in range(threads):
        start = i * seg_size
        end = (start + seg_size - 1) if i < threads - 1 else (size_bytes - 1)
        segments.append((f"{start}-{end}", f"{local_path}.part{i}"))

    logger.info(f"sftp ↓ start  {filename}  {size_bytes/1024/1024:.1f} MB  {threads} segments")

    errors: list[str] = []
    procs: list[subprocess.Popen] = []

    def _run_seg(byte_range: str, seg_path: str) -> None:
        cmd = _curl_sftp_base() + ["--output", seg_path, "--range", byte_range, url]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        procs.append(p)
        _, stderr = p.communicate()
        procs.remove(p)
        if p.returncode != 0:
            errors.append(stderr.decode(errors="replace").strip())

    threads_list = [
        threading.Thread(target=_run_seg, args=(br, sp), daemon=True)
        for br, sp in segments
    ]
    for t in threads_list:
        t.start()

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

    logger.info(f"sftp ↓ merge  {filename}")
    with open(local_path, "wb") as out:
        for _, seg_path in segments:
            with open(seg_path, "rb") as seg:
                while chunk := seg.read(1024 * 1024):
                    out.write(chunk)
            os.remove(seg_path)

    elapsed = max(time.monotonic() - t_start, 0.001)
    logger.info(f"sftp ↓ done  {filename}  avg {(size_bytes/1024/1024)/elapsed:.1f} MB/s  ({elapsed:.0f}s)")


# ── qBittorrent source ────────────────────────────────────────────────────────

class QbittorrentSource(BaseSource):
    """Polls qBittorrent via Web API; downloads via curl (SFTP)."""

    def __init__(self):
        self._client = _QbtClient()

    def is_configured(self) -> bool:
        return bool(
            settings.qbittorrent_enabled
            and settings.qbittorrent_url
            and settings.qbittorrent_user
            and settings.qbittorrent_ssh_host
        )

    @property
    def default_category(self) -> str:
        return settings.qbittorrent_category

    def _get_torrents(self, category: str = "") -> list[dict]:
        import json
        params = {"category": category} if category else {}
        return json.loads(self._client.get("/api/v2/torrents/info", params))

    def _get_files(self, hash_: str) -> list[dict]:
        import json
        return json.loads(self._client.get("/api/v2/torrents/files", {"hash": hash_}))

    def _make_item(self, t: dict, files: list[dict]) -> SourceItem:
        name = t["name"]
        content_path = t["content_path"]
        save_path = t["save_path"].rstrip("/")
        hash_ = t["hash"].upper()

        # Multi-file: content_path is the torrent folder (save_path/name).
        # Single-file: content_path is the actual file (save_path/filename.ext).
        is_multi = content_path == f"{save_path}/{name}"
        file_paths = [f["name"] for f in files]

        return SourceItem(
            id=hash_,
            name=name,
            remote_path=content_path,
            suggested_type=detect_type(t.get("category", ""), file_paths, name),
            size_bytes=int(t.get("size", 0)),
            metadata={
                "category":     t.get("category", ""),
                "files":        file_paths,
                "is_multi":     is_multi,
                "save_path":    save_path,
                "content_path": content_path,
            },
        )

    # ── BaseSource interface ──────────────────────────────────────────────────

    def list_all_brief(self) -> dict[str, dict]:
        """All torrents as {HASH: {name, label, pct}} — for presence detection."""
        result = {}
        for t in self._get_torrents():
            result[t["hash"].upper()] = {
                "name":       t["name"],
                "label":      t.get("category", ""),
                "pct":        round(float(t.get("progress", 0)) * 100, 1),
                "size_bytes": int(t.get("size", 0)),
            }
        return result

    def check_new_completions(self, category: str,
                              exclude_ids: set[str]) -> list[tuple[str, str]]:
        result = []
        for t in self._get_torrents(category=category):
            h = t["hash"].upper()
            if (t.get("state") in _COMPLETE_STATES
                    or float(t.get("progress", 0)) >= 1.0) and h not in exclude_ids:
                result.append((h, t["name"]))
        return result

    def list_ready(self, exclude_ids: set | None = None) -> list[SourceItem]:
        category = settings.qbittorrent_category
        exclude_ids = exclude_ids or set()
        t0 = time.monotonic()

        logger.info(f"Connecting to qBittorrent: {settings.qbittorrent_url}")
        torrents = self._get_torrents(category=category)
        logger.info(f"qBittorrent: {len(torrents)} torrents in category '{category}' ({time.monotonic()-t0:.2f}s)")

        items: list[SourceItem] = []
        skipped = 0
        for t in torrents:
            h = t["hash"].upper()
            if not (t.get("state") in _COMPLETE_STATES or float(t.get("progress", 0)) >= 1.0):
                continue
            if h in exclude_ids:
                skipped += 1
                continue
            files = self._get_files(t["hash"])
            items.append(self._make_item(t, files))

        logger.info(f"list_ready: {len(items)} new, {skipped} skipped [{time.monotonic()-t0:.2f}s total]")
        return items

    def get_item_by_hash(self, hash_: str) -> SourceItem:
        import json
        raw = self._client.get("/api/v2/torrents/info", {"hashes": hash_.lower()})
        torrents = json.loads(raw)
        if not torrents:
            raise ValueError(f"Torrent {hash_} not found in qBittorrent")
        t = torrents[0]
        if float(t.get("progress", 0)) < 1.0:
            raise ValueError(f"Torrent is not complete ({float(t.get('progress',0))*100:.1f}%)")
        return self._make_item(t, self._get_files(t["hash"]))

    def load_torrent(self, torrent_bytes: bytes, label: str = "") -> None:
        """Add a .torrent file to qBittorrent with the specified category."""
        category = label or settings.qbittorrent_category
        self._client.post_multipart(
            "/api/v2/torrents/add",
            fields={"category": category, "stopped": "false", "paused": "false"},
            files={"torrents": ("upload.torrent", torrent_bytes)},
        )
        logger.info(f"Loaded torrent into qBittorrent with category={category!r}")

    def stop_torrent(self, hash_: str) -> None:
        self._client.post("/api/v2/torrents/stop", {"hashes": hash_.lower()})
        logger.info(f"Stopped torrent {hash_}")

    def mark_done(self, item: SourceItem) -> None:
        pass  # import state tracked locally in synced_items DB

    def list_active(self) -> list[dict]:
        """In-progress torrents in the watch category."""
        active = []
        for t in self._get_torrents(category=settings.qbittorrent_category):
            progress = float(t.get("progress", 0))
            state = t.get("state", "")
            if state in _COMPLETE_STATES or progress >= 1.0:
                continue
            size = int(t.get("size", 1))
            active.append({
                "hash":       t["hash"].upper(),
                "name":       t["name"],
                "label":      t.get("category", ""),
                "size_bytes": size,
                "bytes_done": int(t.get("downloaded", 0)),
                "pct":        round(progress * 100, 1),
                "down_rate":  int(t.get("dlspeed", 0)),
                "up_rate":    int(t.get("upspeed", 0)),
                "is_active":  state not in ("stalledDL", "pausedDL", "stoppedDL"),
            })
        return active

    def download(self, item: SourceItem, dest_dir: str, progress_cb=None,
                 cancel_check=None) -> None:
        """Download via curl SFTP. Parallel segments for single files; per-file for multi."""
        content_path = item.metadata["content_path"]
        save_path    = item.metadata["save_path"]
        is_multi     = item.metadata.get("is_multi", False)
        file_paths   = item.metadata.get("files", [])
        total_bytes  = max(item.size_bytes, 1)
        threads      = settings.qbittorrent_threads
        name         = item.name

        os.makedirs(dest_dir, exist_ok=True)
        logger.info(f"sftp download: {name}  is_multi={is_multi}  size={total_bytes/1024/1024:.1f} MB")

        t_start  = time.monotonic()
        last_size = [0]
        last_t    = [t_start]

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
            dt  = max(now - last_t[0], 0.001)
            mbps = (current - last_size[0]) / 1024 / 1024 / dt
            last_size[0] = current
            last_t[0]    = now
            progress_cb(min(int(current / total_bytes * 100), 99), name, mbps)

        if not is_multi:
            local_path = os.path.join(dest_dir, os.path.basename(content_path))
            done_event = threading.Event()

            def _progress_loop():
                while not done_event.wait(timeout=2.0):
                    _report_progress()

            prog = threading.Thread(target=_progress_loop, daemon=True)
            prog.start()
            try:
                _download_single_sftp(content_path, local_path, total_bytes, threads,
                                      cancel_check=cancel_check)
            finally:
                done_event.set()
                prog.join(timeout=3)
        else:
            if not file_paths:
                raise RuntimeError(f"No file list for multi-file torrent {name}")
            if item.suggested_type == "movies":
                file_paths = _filter_movie_files(file_paths)

            # file_paths are relative to save_path
            transfers = [
                (f"{save_path}/{f}", os.path.join(dest_dir, f))
                for f in file_paths
            ]
            logger.info(f"sftp multi: {name}  {len(transfers)} file(s)  {threads} concurrent")

            active_procs: list[subprocess.Popen] = []
            lock = threading.Lock()
            done_event = threading.Event()

            def _progress_loop():
                while not done_event.wait(timeout=2.0):
                    _report_progress()

            prog = threading.Thread(target=_progress_loop, daemon=True)
            prog.start()

            def _dl_one(remote: str, local: str) -> None:
                os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
                cmd = _curl_sftp_base() + ["--output", local, _sftp_url(remote)]
                p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                with lock:
                    active_procs.append(p)
                _, stderr = p.communicate()
                with lock:
                    if p in active_procs:
                        active_procs.remove(p)
                if p.returncode != 0:
                    raise RuntimeError(
                        f"curl sftp failed ({p.returncode}): "
                        f"{stderr.decode(errors='replace').strip()}"
                    )

            def _kill_all():
                with lock:
                    for p in list(active_procs):
                        try:
                            p.terminate()
                        except Exception:
                            pass

            try:
                with ThreadPoolExecutor(max_workers=threads) as pool:
                    futures = {pool.submit(_dl_one, r, l): (r, l) for r, l in transfers}
                    for future in as_completed(futures):
                        if cancel_check and cancel_check():
                            _kill_all()
                            pool.shutdown(wait=False, cancel_futures=True)
                            raise InterruptedError("Download cancelled")
                        future.result()
            except InterruptedError:
                _kill_all()
                raise
            finally:
                done_event.set()
                prog.join(timeout=3)

        elapsed = max(time.monotonic() - t_start, 0.001)
        avg_mbps = (total_bytes / 1024 / 1024) / elapsed
        logger.info(f"sftp done  {name}  avg {avg_mbps:.1f} MB/s  ({elapsed:.0f}s)")
        if progress_cb:
            progress_cb(100, name, avg_mbps)
