from __future__ import annotations

from threading import Lock

from tqdm import tqdm

from b2t.i18n import tr
from b2t.models import ProgressSnapshot


class TqdmTaskRenderer:
    def __init__(self, language: str) -> None:
        self.language = language
        self._bar = tqdm(
            total=100, leave=False, dynamic_ncols=True,
            bar_format="{desc}: {percentage:5.1f}%|{bar}| {n:.1f}/{total:.0f} [{elapsed}]",
        )
        self._lock = Lock()
        self._last_stage = ""

    def __call__(self, snapshot: ProgressSnapshot) -> None:
        with self._lock:
            stage_label = tr(self.language, f"progress_stage_{snapshot.stage}")
            message = tr(self.language, f"progress_message_{snapshot.message}")
            description = stage_label if message == f"progress_message_{snapshot.message}" else f"{stage_label} | {message}"
            if "processed_seconds" in snapshot.detail and "total_seconds" in snapshot.detail:
                description += " · " + tr(
                    self.language, "progress_audio_time",
                    processed=_format_audio_time(float(snapshot.detail["processed_seconds"])),
                    total=_format_audio_time(float(snapshot.detail["total_seconds"])),
                )

            if snapshot.stage != self._last_stage:
                self._bar.write(description)
                self._last_stage = snapshot.stage

            self._bar.set_description_str(description)
            target = max(0.0, min(1.0, snapshot.percent)) * 100
            if target < self._bar.n:
                self._bar.reset()
            self._bar.n = target
            self._bar.refresh()

            if snapshot.status in {"completed", "failed", "cancelled"}:
                self._bar.leave = True
                self._bar.refresh()
                self._bar.close()


def _format_audio_time(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remaining = divmod(total, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
