import logging
import os
import re
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

        cmd = [
            "7z", "x", archive_path,
            f"-o{source_path}",
            "-y",       # overwrite without prompt
            "-bsp1",    # progress → stdout
            "-bse0",    # suppress error stream
            "-bso0",    # suppress normal output
        ]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in process.stdout:
                m = re.match(r"^\s*(\d+)%", line)
                if m:
                    file_pct = int(m.group(1))
                    scaled = base_pct + int(file_pct / 100 * (end_pct - base_pct))
                    update_job(
                        job_id,
                        progress=scaled,
                        message=f"[{name}] {file_pct}%",
                    )
            process.wait()
        except FileNotFoundError:
            update_job(
                job_id,
                status="error",
                message="7z not found. Ensure p7zip-full is installed in the container.",
            )
            return
        except Exception as exc:
            update_job(job_id, status="error", message=str(exc))
            return

        # 7z exit codes: 0=ok, 1=warning, 2+=fatal
        if process.returncode > 1:
            update_job(
                job_id,
                status="error",
                message=f"7z failed with exit code {process.returncode} on {name}",
            )
            return

    update_job(job_id, status="done", progress=100, message="Extraction complete.")


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
