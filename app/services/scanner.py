import os
import re
from typing import Any


def get_categories(incoming_dir: str) -> list[dict[str, Any]]:
    if not os.path.isdir(incoming_dir):
        return []
    result = []
    for name in sorted(os.listdir(incoming_dir)):
        path = os.path.join(incoming_dir, name)
        if not os.path.isdir(path):
            continue
        items = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
        result.append({"name": name, "path": path, "item_count": len(items)})
    return result


def get_items(category_path: str) -> list[dict[str, Any]]:
    if not os.path.isdir(category_path):
        return []
    result = []
    for name in sorted(os.listdir(category_path)):
        item_path = os.path.join(category_path, name)
        if not os.path.isdir(item_path):
            continue
        has_rar = _dir_has_rar(item_path)
        size = _dir_size(item_path)
        result.append({
            "name": name,
            "path": item_path,
            "has_rar": has_rar,
            "size_bytes": size,
            "size_human": _human_size(size),
        })
    return result


def get_item_details(item_path: str) -> dict[str, Any]:
    files = []
    rar_archives = []
    try:
        entries = sorted(os.scandir(item_path), key=lambda e: (e.is_dir(), e.name))
    except PermissionError:
        return {"files": [], "has_rar": False, "rar_archives": []}

    for entry in entries:
        is_dir = entry.is_dir(follow_symlinks=False)
        size = 0 if is_dir else entry.stat().st_size
        files.append({
            "name": entry.name,
            "is_dir": is_dir,
            "size_bytes": size,
            "size_human": _human_size(size),
        })
        if not is_dir and _is_main_rar(entry.name):
            rar_archives.append(entry.name)

    return {
        "files": files,
        "has_rar": bool(rar_archives),
        "rar_archives": rar_archives,
    }


def _dir_has_rar(path: str) -> bool:
    try:
        return any(f.lower().endswith(".rar") for f in os.listdir(path))
    except PermissionError:
        return False


def _is_main_rar(filename: str) -> bool:
    """True for .rar and .part1.rar/.part01.rar — skip continuation parts."""
    fl = filename.lower()
    if not fl.endswith(".rar"):
        return False
    if re.search(r"\.part0*[2-9]\d*\.rar$", fl):
        return False
    return True


def _dir_size(path: str) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += _dir_size(entry.path)
    except PermissionError:
        pass
    return total


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
