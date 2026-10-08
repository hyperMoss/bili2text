from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.downloaders.base import Downloader
from b2t.downloaders.ytdlp import YtDlpDownloader
from b2t.inputs import parse_source
from b2t.models import DownloadResult, SourceRef
from b2t.progress import ProgressCallback, ProgressReporter


@dataclass(slots=True)
class DownloadPlanItem:
    source: SourceRef
    path: Path
    skip: bool


def media_duration(path: Path) -> float | None:
    """Only accept readable media with both video and audio tracks."""
    if not path.is_file():
        return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to validate downloaded videos")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        return None
    try:
        data = json.loads(result.stdout)
        tracks = {stream.get("codec_type") for stream in data.get("streams", [])}
        duration = float(data.get("format", {}).get("duration", 0))
        return duration if {"video", "audio"} <= tracks and duration > 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


def download_error_message(error: Exception) -> str:
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    return re.sub(r"https?://\S+", "<URL>", message)


class DownloadBatchService(Downloader):
    """Plan and execute resumable downloads without constructing a transcriber."""

    name = "yt-dlp"

    def __init__(self, settings: Settings, database: AppDatabase, *, attempts: int = 3) -> None:
        self.settings = settings
        self.database = database
        self.downloader = YtDlpDownloader()
        self.attempts = attempts

    def plan(self, sources: list[str], *, from_tasks: bool = False) -> list[DownloadPlanItem]:
        refs = [parse_source(source) for source in sources]
        if any(ref.kind != "bilibili" for ref in refs):
            raise ValueError("download only accepts Bilibili BV IDs or video URLs")
        if from_tasks:
            for task in reversed(self.database.list_tasks()):
                if task.kind not in {"transcription", "download"}:
                    continue
                try:
                    source = parse_source(task.source_input)
                except ValueError:
                    continue
                if source.kind == "bilibili":
                    refs.append(source)
        if not refs:
            raise ValueError("no Bilibili sources found; supply inputs, --file, or --from-tasks")
        plan = []
        seen = set()
        for ref in refs:
            key = (ref.bv, ref.page or 1)
            if key in seen:
                continue
            seen.add(key)
            # A bare BV and an explicit p=1 refer to the same first part.
            if ref.page == 1:
                ref = parse_source(ref.bv or ref.raw_input)
            path = self.downloader.output_path(ref, self.settings)
            plan.append(DownloadPlanItem(ref, path, media_duration(path) is not None))
        return plan

    def cached_result(self, item: DownloadPlanItem) -> DownloadResult:
        metadata_path = self.settings.tasks_dir / "downloaded-media" / f"{item.path.stem}.json"
        metadata = {}
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return DownloadResult(
            source=item.source, video_path=item.path,
            title=metadata.get("title") or item.source.display_name,
            webpage_url=item.source.url, metadata=metadata,
        )

    def fetch_subtitle(self, source: SourceRef, settings: Settings, *, progress=None):
        return self.downloader.fetch_subtitle(source, settings, progress=progress)

    def download(self, source: SourceRef, settings: Settings, *, progress=None) -> DownloadResult:
        """Pipeline adapter: defer resume/validation until subtitle lookup misses."""
        path = self.downloader.output_path(source, settings)
        item = DownloadPlanItem(source, path, media_duration(path) is not None)
        if item.skip:
            return self.cached_result(item)

        def forward(snapshot):
            if progress is not None and snapshot.status == "running":
                progress.emit(status="running", stage=snapshot.stage, message=snapshot.message,
                              percent=snapshot.percent, indeterminate=snapshot.indeterminate, detail=snapshot.detail)

        return self.run_one(item, progress=forward)

    def has_transcript(self, item: DownloadPlanItem, *, provider: str, model: str, audio_language: str | None = None) -> bool:
        for video in self.database.list_videos():
            if video["engine"] == "bilibili-subtitles":
                try:
                    source = parse_source(video["source_input"])
                except ValueError:
                    continue
                if (source.bv, source.page or 1) != (item.source.bv, item.source.page or 1):
                    continue
            else:
                if video["engine"] != provider or video["model"] != model:
                    continue
                if audio_language and audio_language != "auto" and video.get("language") != audio_language.strip().lower():
                    continue
                path = video.get("video_path")
                if not path or Path(str(path)).resolve() != item.path.resolve():
                    continue
            version = self.database.get_active_transcript_version(int(video["id"]))
            if version is not None:
                transcript = Path(version.file_path)
                if transcript.is_file() and transcript.read_text(encoding="utf-8").strip():
                    return True
        return False

    def run_one(self, item: DownloadPlanItem, *, progress: ProgressCallback | None = None) -> DownloadResult:
        task = self.database.create_task(kind="download", source_input=item.source.raw_input, provider="yt-dlp", model="")
        last_written = 0.0
        last_stage = ""

        def publish(snapshot):
            nonlocal last_written, last_stage
            now = time.monotonic()
            if snapshot.status != "running" or snapshot.stage != last_stage or now - last_written >= 0.5:
                self.database.record_progress(snapshot)
                last_written, last_stage = now, snapshot.stage
            if progress is not None:
                progress(snapshot)

        reporter = ProgressReporter(task.id, callback=publish)

        def preserve_invalid(path: Path) -> None:
            if path.is_file():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                path.rename(path.with_name(f"{path.name}.invalid-{stamp}"))

        try:
            reporter.running("preparing", message="preparing")
            # yt-dlp skips existing final files; move an invalid one aside so
            # retrying can actually replace it, while keeping its original bytes.
            if item.path.exists() and media_duration(item.path) is None:
                preserve_invalid(item.path)
            for attempt in range(1, self.attempts + 1):
                try:
                    result = self.downloader.download(item.source, self.settings, progress=reporter)
                    duration = media_duration(result.video_path)
                    expected = result.metadata.get("duration")
                    if duration is None or (expected and duration + 5 < float(expected)):
                        preserve_invalid(result.video_path)
                        raise RuntimeError("downloaded video failed audio/video/duration validation")
                    metadata_path = self.settings.tasks_dir / "downloaded-media" / f"{item.path.stem}.json"
                    metadata_path.parent.mkdir(parents=True, exist_ok=True)
                    metadata_path.write_text(json.dumps({**result.metadata, "title": result.title}, ensure_ascii=False, indent=2), encoding="utf-8")
                    reporter.completed("completed")
                    self.database.complete_task(task.id, message=str(result.video_path))
                    return result
                except Exception:
                    if attempt == self.attempts:
                        raise
                    reporter.running("preparing", message=f"retry {attempt + 1}/{self.attempts}")
                    time.sleep(min(3 * attempt, 10))
        except KeyboardInterrupt:
            reporter.emit(status="cancelled", stage="cancelled", message="cancelled", percent=reporter.snapshot.percent)
            self.database.cancel_task(task.id, message="interrupted; partial files preserved")
            raise
        except Exception as error:
            message = download_error_message(error)
            reporter.failed(message)
            self.database.fail_task(task.id, error_message=message)
            raise RuntimeError(message) from error
        raise RuntimeError("no download attempts were made")
