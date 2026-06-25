import logging
import os
import re
import shutil
import subprocess

from .job_manager import update_job

logger = logging.getLogger(__name__)


def run_extraction(job_id: int, source_path: str) -> None:
    update_job(job_id, status="running", progress=2, message="Scanning for archives...")

    archives = _find_main_archives(source_path)
    if not archives:
        update_job(job_id, status="error", message="No RAR archives found in directory")
        return

    total = len(archives)
    update_job(job_id, progress=5, message=f"Found {total} archive(s). Starting extraction...")

    for idx, archive_path in enumerate(archives):
        name = os.path.basename(archive_path)
        base_pct = 5 + int(idx / total * 90)
        end_pct = 5 + int((idx + 1) / total * 90)

        update_job(job_id, progress=base_pct, message=f"Extracting {name}...")

        ok, err = _extract_7z(archive_path, source_path, job_id, base_pct, end_pct)

        if not ok and name.lower().endswith(".rar") and shutil.which("unrar"):
            update_job(job_id, progress=base_pct, message=f"7z failed, retrying with unrar: {name}...")
            logger.info(f"7z failed on {name} ({err}), falling back to unrar")
            ok, err = _extract_unrar(archive_path, source_path, job_id, base_pct, end_pct)

        if not ok:
            update_job(job_id, status="error", message=err)
            return

    removed = _remove_rar_files(source_path)
    _fix_permissions(source_path)
    update_job(job_id, status="done", progress=100, message=f"Extraction complete. Removed {removed} archive file(s).")


def _extract_7z(archive_path, dest, job_id, base_pct, end_pct):
    name = os.path.basename(archive_path)
    cmd = [
        "7z", "x", archive_path,
        f"-o{dest}",
        "-y", "-bsp1", "-bse0", "-bso0",
    ]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            m = re.match(r"^\s*(\d+)%", line)
            if m:
                file_pct = int(m.group(1))
                scaled = base_pct + int(file_pct / 100 * (end_pct - base_pct))
                update_job(job_id, progress=scaled, message=f"[{name}] {file_pct}%")
        process.wait()
    except FileNotFoundError:
        return False, "7z not found. Ensure p7zip-full is installed in the container."
    except Exception as exc:
        return False, str(exc)

    if process.returncode > 1:
        return False, f"7z failed with exit code {process.returncode} on {name}"
    return True, None


def _extract_unrar(archive_path, dest, job_id, base_pct, end_pct):
    name = os.path.basename(archive_path)
    cmd = ["unrar", "x", "-o+", "-y", archive_path, dest + "/"]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            m = re.search(r"(\d+)%", line)
            if m:
                file_pct = int(m.group(1))
                scaled = base_pct + int(file_pct / 100 * (end_pct - base_pct))
                update_job(job_id, progress=scaled, message=f"[unrar] [{name}] {file_pct}%")
        process.wait()
    except FileNotFoundError:
        return False, "unrar not found."
    except Exception as exc:
        return False, str(exc)

    if process.returncode != 0:
        return False, f"unrar failed with exit code {process.returncode} on {name}"
    return True, None


def _fix_permissions(directory: str) -> None:
    """Set 755 on dirs and 644 on files so other processes (Plex, etc.) can access them."""
    try:
        for root, dirs, files in os.walk(directory):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o644)
                except OSError:
                    pass
    except Exception as exc:
        logger.warning(f"Permission fix failed (non-fatal): {exc}")


def _remove_rar_files(directory: str) -> int:
    """Delete all .rar and .rNN split files after successful extraction."""
    count = 0
    try:
        for name in os.listdir(directory):
            if re.search(r"\.(rar|r\d+)$", name, re.IGNORECASE):
                try:
                    os.remove(os.path.join(directory, name))
                    count += 1
                except OSError as exc:
                    logger.warning(f"Could not remove {name}: {exc}")
    except PermissionError:
        pass
    return count


def _find_main_archives(directory: str) -> list[str]:
    """Return paths to the leading archive in each multi-part set, sorted."""
    result = []
    try:
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(".rar"):
                continue
            # Skip continuation parts (.part2.rar, .part02.rar, …)
            if re.search(r"\.part0*[2-9]\d*\.rar$", name, re.IGNORECASE):
                continue
            result.append(os.path.join(directory, name))
    except PermissionError:
        pass
    return result
