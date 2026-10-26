from __future__ import annotations

import shutil
from threading import Lock

from rich.text import Text
from tqdm import tqdm

from b2t.i18n import tr
from b2t.models import ProgressSnapshot


class TqdmTaskRenderer:
    def __init__(self, language: str, *, label: str = "", position: int | None = None) -> None:
        self.language = language
        self.label = label
        self._bar = tqdm(
            total=100, leave=False, ncols=self._terminal_width(), position=position,
            bar_format="{desc}: {percentage:5.1f}%|{bar}| [{elapsed}]",
        )
        self._lock = Lock()

    @staticmethod
    def _terminal_width() -> int:
        return max(20, shutil.get_terminal_size().columns - 1)

    def __call__(self, snapshot: ProgressSnapshot) -> None:
        with self._lock:
            self._bar.ncols = self._terminal_width()
            stage_label = tr(self.language, f"progress_stage_{snapshot.stage}")
            message = tr(self.language, f"progress_message_{snapshot.message}")
            description = stage_label if snapshot.message == snapshot.stage or message == f"progress_message_{snapshot.message}" else f"{stage_label} | {message}"
            if self.label:
                label = Text(self.label)
                label.truncate(max(8, min(24, self._bar.ncols - 60)), overflow="ellipsis")
                description = f"{label.plain} · {description}"
            if "processed_seconds" in snapshot.detail and "total_seconds" in snapshot.detail:
                description += " · " + tr(
                    self.language, "progress_audio_time",
                    processed=_format_audio_time(float(snapshot.detail["processed_seconds"])),
                    total=_format_audio_time(float(snapshot.detail["total_seconds"])),
                )

            self._bar.set_description_str(description, refresh=False)
            target = max(0.0, min(1.0, snapshot.percent)) * 100
            self._bar.n = target
            self._bar.refresh()

            if snapshot.status in {"completed", "failed", "cancelled"}:
                self._bar.leave = True
                self._bar.close()

    def close(self) -> None:
        with self._lock:
            self._bar.close()


def _format_audio_time(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remaining = divmod(total, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
