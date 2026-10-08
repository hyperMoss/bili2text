from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from b2t.config import Settings
from b2t.downloaders.base import Downloader
from b2t.inputs import parse_source, safe_stem
from b2t.models import DownloadResult, SourceRef, SubtitleResult, TranscriptResult
from b2t.progress import ProgressReporter
from b2t.transcribers.base import Transcriber
from b2t.transcript_segments import normalize_whisper_segments, text_with_segment_breaks, simplify_chinese


class B2TPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        downloader: Downloader,
        transcriber: Transcriber | None = None,
        transcriber_factory: Callable[[], Transcriber] | None = None,
        simplify_whisper: bool = True,
    ) -> None:
        self.settings = settings
        self.downloader = downloader
        self._transcriber = transcriber
        self.transcriber_factory = transcriber_factory
        self.simplify_whisper = simplify_whisper

    @property
    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            if self.transcriber_factory is None:
                raise RuntimeError("no transcriber configured for the ASR fallback")
            self._transcriber = self.transcriber_factory()
        return self._transcriber

    def transcribe(
        self,
        source_input: str,
        *,
        prompt: str | None = None,
        output: Path | None = None,
        progress: ProgressReporter | None = None,
        downloaded: DownloadResult | None = None,
    ) -> TranscriptResult:
        self.settings.ensure_directories()
        if progress is not None:
            progress.running("preparing", message="preparing")
        source = parse_source(source_input)
        if source.kind == "bilibili":
            if downloaded is not None and (downloaded.source.bv, downloaded.source.page or 1) != (source.bv, source.page or 1):
                raise ValueError("downloaded video does not match the requested source")
            subtitle = self.downloader.fetch_subtitle(source, self.settings, progress=progress)
            if subtitle is not None:
                return self._write_subtitle(source, subtitle, output=output, progress=progress)
            if downloaded is None:
                downloaded = self.downloader.download(source, self.settings, progress=progress)
            audio_path = self._extract_audio(
                downloaded.video_path,
                safe_stem(downloaded.title or source.display_name),
                progress=progress,
            )
            base_name = downloaded.title or source.display_name
            video_path = downloaded.video_path
        elif source.kind == "video":
            if downloaded is not None:
                raise ValueError("a downloaded result can only be used with a Bilibili source")
            assert source.path is not None
            audio_path = self._extract_audio(source.path, safe_stem(source.display_name), progress=progress)
            base_name = source.display_name
            video_path = source.path
        else:
            if downloaded is not None:
                raise ValueError("a downloaded result can only be used with a Bilibili source")
            assert source.path is not None
            audio_path = source.path
            base_name = source.display_name
            video_path = None

        transcription = self.transcriber.transcribe(audio_path, prompt=prompt, progress=progress)
        text = transcription.get("text", "").strip()
        if not text:
            raise RuntimeError("transcriber returned an empty transcript")

        segments = []
        recognized_text = text
        if self.transcriber.name == "whisper":
            segments = normalize_whisper_segments(transcription.get("segments"))
            text = text_with_segment_breaks(text, segments)
            if self.simplify_whisper:
                text = simplify_chinese(text)

        metadata = {
            "source": {
                "raw_input": source.raw_input,
                "kind": source.kind,
                "bv": source.bv,
                "url": source.url,
                "path": str(source.path) if source.path else None,
                "page": source.page,
            },
            "engine": self.transcriber.name,
            "model": transcription.get("model"),
            "audio_path": str(audio_path),
            "video_path": str(video_path) if video_path else None,
            "download": downloaded.metadata if downloaded else {},
            "language": transcription.get("language"),
            "generated_at": datetime.now().isoformat(),
        }
        if segments:
            metadata["segments"] = segments
        if self.transcriber.name == "whisper":
            metadata.update(text=recognized_text, device=transcription.get("device"),
                            initial_prompt=transcription.get("initial_prompt"),
                            output_script="simplified" if self.simplify_whisper else "original",
                            format="whisper-sentence-lines-with-timing-fallback")
        return self._write_result(source, text, base_name, metadata, audio_path=audio_path,
                                  video_path=video_path, output=output, progress=progress)

    def _write_subtitle(
        self, source: SourceRef, subtitle: SubtitleResult, *,
        output: Path | None, progress: ProgressReporter | None,
    ) -> TranscriptResult:
        metadata = {
            "source": {"raw_input": source.raw_input, "kind": source.kind, "bv": source.bv,
                       "url": source.url, "path": None, "page": source.page},
            "engine": "bilibili-subtitles", "model": "", "language": subtitle.language,
            "audio_path": None, "video_path": None, "download": subtitle.metadata,
            "generated_at": datetime.now().isoformat(),
        }
        result = self._write_result(source, subtitle.text, subtitle.title or source.display_name,
                                    metadata, audio_path=None, video_path=None, output=output, progress=progress)
        subtitle_path = result.transcript_path.with_suffix(".srt")
        subtitle_path.write_text(subtitle.content, encoding="utf-8")
        result.metadata["subtitle_path"] = str(subtitle_path)
        result.metadata_path.write_text(json.dumps(result.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _write_result(
        self, source: SourceRef, text: str, base_name: str, metadata: dict[str, Any], *,
        audio_path: Path | None, video_path: Path | None, output: Path | None,
        progress: ProgressReporter | None,
    ) -> TranscriptResult:
        if progress is not None:
            progress.running("writing_outputs", message="writing_outputs", indeterminate=True)
        transcript_path = self._resolve_output_path(base_name, output)
        metadata_path = self._resolve_metadata_path(transcript_path)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(text + "\n", encoding="utf-8")
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return TranscriptResult(
            source=source, engine=metadata["engine"], model=str(metadata.get("model") or ""),
            text=text, audio_path=audio_path, transcript_path=transcript_path,
            metadata_path=metadata_path, video_path=video_path, metadata=metadata,
        )

    def _extract_audio(self, video_path: Path, stem: str, progress: ProgressReporter | None = None) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to extract audio but was not found on PATH")

        audio_path = self.settings.audio_dir / f"{stem}.wav"
        if progress is None:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(audio_path),
                ],
                capture_output=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                stderr = result.stderr.strip() or "unknown ffmpeg error"
                raise RuntimeError(f"ffmpeg failed to extract audio: {stderr}")
            return audio_path

        duration = _probe_media_duration_seconds(video_path)
        progress.running(
            "extracting_audio",
            message="extracting_audio",
            stage_progress=0.0 if duration else None,
            indeterminate=duration is None,
        )
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-progress",
            "pipe:1",
            "-nostats",
            str(audio_path),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
        )
        assert process.stdout is not None
        for line in process.stdout:
            parsed_seconds = _parse_ffmpeg_progress_seconds(line.strip())
            if parsed_seconds is None or duration in (None, 0):
                continue
            progress.running(
                "extracting_audio",
                message="extracting_audio",
                stage_progress=min(1.0, parsed_seconds / duration),
            )
        stderr_text = ""
        if process.stderr is not None:
            stderr_text = process.stderr.read()
        returncode = process.wait()
        if returncode != 0:
            stderr = stderr_text.strip() or "unknown ffmpeg error"
            raise RuntimeError(f"ffmpeg failed to extract audio: {stderr}")
        return audio_path

    def _resolve_output_path(self, base_name: str, output: Path | None) -> Path:
        if output is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            return self.settings.transcripts_original_dir / f"{safe_stem(base_name)}-{timestamp}.txt"

        output = output.expanduser()
        if output.suffix.lower() != ".txt":
            if output.exists() and output.is_dir():
                return output / f"{safe_stem(base_name)}.txt"
            return output.with_suffix(".txt")
        return output

    def _resolve_metadata_path(self, transcript_path: Path) -> Path:
        if transcript_path.is_relative_to(self.settings.workspace_root):
            return self.settings.metadata_dir / f"{transcript_path.stem}.json"
        return transcript_path.with_suffix(".json")


def _parse_ffmpeg_progress_seconds(line: str) -> float | None:
    if line.startswith("out_time_ms="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time_us="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    return None


def _probe_media_duration_seconds(video_path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    try:
        value = float((result.stdout or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None
