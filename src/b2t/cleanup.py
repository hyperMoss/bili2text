from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil


@dataclass(frozen=True, slots=True)
class DownloadCleanupPlan:
    root: Path
    entries: tuple[Path, ...]
    file_count: int
    total_bytes: int


def plan_download_cleanup(downloads_dir: Path) -> DownloadCleanupPlan:
    root = downloads_dir.resolve()
    if not root.is_dir():
        return DownloadCleanupPlan(root=root, entries=(), file_count=0, total_bytes=0)

    file_count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if not path.is_file() and not path.is_symlink():
            continue
        file_count += 1
        if not path.is_symlink():
            total_bytes += path.stat().st_size
    return DownloadCleanupPlan(
        root=root,
        entries=tuple(sorted(root.iterdir(), key=lambda path: path.name)),
        file_count=file_count,
        total_bytes=total_bytes,
    )


def clear_downloads(plan: DownloadCleanupPlan) -> int:
    plan.root.mkdir(parents=True, exist_ok=True)
    for path in plan.entries:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
    return plan.file_count


def format_bytes(size: int) -> str:
    amount = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")
