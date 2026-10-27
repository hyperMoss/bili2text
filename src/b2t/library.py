from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from b2t.fileio import atomic_write_text
from b2t.integrity import _metadata_source, _normal_source
from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.inputs import safe_stem
from b2t.models import TranscriptResult


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WorkspaceLibrary:
    def __init__(self, settings: Settings, database: AppDatabase) -> None:
        self.settings = settings
        self.database = database
        self.settings.ensure_directories()

    def register_transcript_result(self, result: TranscriptResult) -> int:
        transcript_path = self._ensure_original_transcript(result)
        metadata_path = self._ensure_metadata_file(result, transcript_path)
        text = transcript_path.read_text(encoding="utf-8")

        video_id = self.database.create_video(
            source_kind=result.source.kind,
            source_input=result.source.raw_input,
            source_url=result.source.url,
            source_bv=result.source.bv,
            title=(result.metadata.get("download") or {}).get("title") or result.source.display_name,
            display_name=result.source.display_name,
            language=result.metadata.get("language"),
            engine=result.engine,
            model=result.model,
            video_path=str(result.video_path) if result.video_path else None,
            audio_path=str(result.audio_path) if result.audio_path else "",
            metadata_path=str(metadata_path),
        )
        if self.database.get_active_transcript_version(video_id) is None:
            self.database.create_transcript_version(
                video_id=video_id,
                kind="original",
                file_path=str(transcript_path),
                text_sha256=sha256_text(text),
                char_count=len(text),
                is_active=True,
            )
        return video_id

    def save_edited_transcript(self, video_id: int, text: str) -> int:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")

        base_name = safe_stem(str(video["display_name"]) or f"video-{video_id}")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.settings.transcripts_edited_dir / f"{base_name}-{video_id}-{timestamp}.txt"
        atomic_write_text(path, text.rstrip() + "\n")
        return self.database.create_transcript_version(
            video_id=video_id,
            kind="edited",
            file_path=str(path),
            text_sha256=sha256_text(text.rstrip() + "\n"),
            char_count=len(text.rstrip() + "\n"),
            is_active=True,
        )

    def load_active_transcript(self, video_id: int) -> dict[str, object]:
        version = self.database.get_active_transcript_version(video_id)
        if version is None:
            raise RuntimeError(f"active transcript not found for video {video_id}")
        return self.load_transcript_version(video_id, version.id)

    def load_transcript_version(self, video_id: int, version_id: int) -> dict[str, object]:
        version = self.database.get_transcript_version(video_id, version_id)
        if version is None:
            raise RuntimeError(f"transcript version not found: video={video_id} version={version_id}")
        path = Path(version.file_path)
        return {
            "version_id": version.id,
            "kind": version.kind,
            "file_path": version.file_path,
            "is_active": version.is_active,
            "text": path.read_text(encoding="utf-8"),
        }

    def load_video_metadata(self, video_id: int) -> dict[str, object]:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")
        metadata_path = Path(str(video["metadata_path"]))
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def index_existing_workspace(self) -> None:
        self.settings.ensure_directories()
        for metadata_path in sorted(self.settings.metadata_dir.glob("*.json")):
            transcript_path = self.settings.transcripts_original_dir / f"{metadata_path.stem}.txt"
            if not transcript_path.exists():
                fallback = metadata_path.with_suffix(".txt")
                if fallback.exists():
                    transcript_path = fallback
            if not transcript_path.exists():
                continue

            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or _metadata_source(data) is None:
                    continue
                declared = data.get("transcript_path")
                if declared and Path(str(declared)).resolve() != transcript_path.resolve():
                    continue
                if data.get("text_sha256") or data.get("char_count") is not None:
                    text = transcript_path.read_text(encoding="utf-8")
                    if data.get("text_sha256") and sha256_text(text) != data["text_sha256"]:
                        continue
                    if data.get("char_count") is not None and len(text) != data["char_count"]:
                        continue
            except (OSError, ValueError, UnicodeError):
                continue
            source = data.get("source") or {}
            video_id = self.database.create_video(
                source_kind=source.get("kind") or "audio",
                source_input=source.get("raw_input") or transcript_path.stem,
                source_url=source.get("url"),
                source_bv=source.get("bv"),
                title=(data.get("download") or {}).get("title") or transcript_path.stem,
                display_name=transcript_path.stem,
                language=data.get("language"),
                engine=data.get("engine") or "unknown",
                model=str(data.get("model") or ""),
                video_path=data.get("video_path"),
                audio_path=data.get("audio_path") or "",
                metadata_path=str(metadata_path),
            )
            if self.database.get_active_transcript_version(video_id) is not None:
                continue
            text = transcript_path.read_text(encoding="utf-8")
            self.database.create_transcript_version(
                video_id=video_id,
                kind="original",
                file_path=str(transcript_path),
                text_sha256=sha256_text(text),
                char_count=len(text),
                is_active=True,
            )

    def _ensure_original_transcript(self, result: TranscriptResult) -> Path:
        target = self.settings.transcripts_original_dir / result.transcript_path.name
        if result.transcript_path.resolve() != target.resolve():
            incoming = result.transcript_path.read_text(encoding="utf-8")
            if target.exists():
                if target.read_text(encoding="utf-8") != incoming:
                    raise FileExistsError(f"original transcript filename collision: {target}")
            else:
                atomic_write_text(target, incoming)
        return target

    def _ensure_metadata_file(self, result: TranscriptResult, transcript_path: Path) -> Path:
        target = self.settings.metadata_dir / f"{transcript_path.stem}.json"
        metadata = {
            **result.metadata,
            "source": {**{"raw_input": result.source.raw_input, "kind": result.source.kind,
                          "bv": result.source.bv, "url": result.source.url,
                          "page": result.source.page,
                          "path": str(result.source.path) if result.source.path else None},
                       **(result.metadata.get("source") or {})},
            "engine": result.engine,
            "model": result.model,
            "audio_path": str(result.audio_path) if result.audio_path else None,
            "video_path": str(result.video_path) if result.video_path else None,
            "transcript_path": str(transcript_path),
        }
        content = transcript_path.read_text(encoding="utf-8")
        metadata["text_sha256"] = sha256_text(content)
        metadata["char_count"] = len(content)
        if target.exists():
            try:
                previous = json.loads(target.read_text(encoding="utf-8"))
                if _metadata_source(previous) != _normal_source(
                    result.source.kind, result.source.raw_input, result.source.bv,
                    result.source.page, result.source.path,
                ):
                    raise ValueError(f"metadata source collision: {target}")
                if any(previous.get(key) != metadata.get(key) for key in
                       ("engine", "model", "transcript_path", "text_sha256")):
                    raise ValueError(f"metadata differs from existing record: {target}")
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError(f"cannot safely reuse metadata: {target}") from exc
        else:
            atomic_write_text(target, json.dumps(metadata, ensure_ascii=False, indent=2))
        return target
