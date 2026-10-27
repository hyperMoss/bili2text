"""Read-only integrity checks for the transcript workspace and SQLite index.

Never creates or repairs files. Safe to run against a live workspace; the
SQLite connection uses mode=ro and query_only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from b2t.config import Settings


def _issue(issues: list[dict[str, object]], code: str, **details: object) -> None:
    issues.append({"code": code, **details})


def _normal_source(kind: object, raw: object, bv: object = None, page: object = None, path: object = None) -> tuple[str, str, int] | None:
    kind = str(kind or "")
    if kind == "bilibili":
        bv_value = str(bv or "")
        raw_value = str(raw or "")
        if not bv_value:
            import re
            found = re.search(r"BV[0-9A-Za-z]{10}", raw_value)
            bv_value = found.group(0) if found else ""
        if not bv_value:
            return None
        if page in (None, "", 0):
            value = parse_qs(urlparse(raw_value).query).get("p", ["1"])[0]
            page = value
        try:
            number = int(page)
        except (ValueError, TypeError):
            return None
        return ("bilibili", bv_value, max(1, number))
    if kind in {"audio", "video"}:
        filename = path or raw
        if filename:
            return (kind, str(Path(str(filename)).expanduser().resolve()), 0)
    return None


def _metadata_source(data: dict[str, Any]) -> tuple[str, str, int] | None:
    source = data.get("source")
    if not isinstance(source, dict):
        return None
    return _normal_source(source.get("kind"), source.get("raw_input"), source.get("bv"),
                          source.get("page"), source.get("path"))


def _record_source(video: dict[str, Any]) -> tuple[str, str, int] | None:
    return _normal_source(video.get("source_kind"), video.get("source_input"), video.get("source_bv"),
                          None, video.get("source_input"))


def metadata_matches_video(data: dict[str, Any], video: dict[str, Any]) -> bool:
    """Reject sources/engines that differ; tolerate legacy optional fields."""
    actual, expected = _metadata_source(data), _record_source(video)
    if actual is None or expected is None or actual != expected:
        return False
    return (
        str(data.get("engine") or "") == str(video.get("engine") or "")
        and str(data.get("model") or "") == str(video.get("model") or "")
        and (not data.get("language") or not video.get("language")
             or data["language"] == video["language"])
    )


def verified_active_transcript(database: Any, video: dict[str, Any]) -> bool:
    """True only for a complete, identity-matched record safe to skip in resume."""
    try:
        metadata_path = Path(str(video["metadata_path"]))
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not metadata_matches_video(data, video):
            return False
        version = database.get_active_transcript_version(int(video["id"]))
        if version is None or int(video["current_transcript_version_id"] or 0) != version.id:
            return False
        path = Path(version.file_path)
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8")
        if not text.strip() or len(text) != version.char_count:
            return False
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != version.text_sha256:
            return False
        originals = [item for item in database.list_transcript_versions(int(video["id"])) if item.kind == "original"]
        if len(originals) != 1:
            return False
        original = originals[0]
        original_path = Path(original.file_path)
        if not original_path.is_file():
            return False
        original_text = original_path.read_text(encoding="utf-8")
        if (hashlib.sha256(original_text.encode("utf-8")).hexdigest() != original.text_sha256
                or len(original_text) != original.char_count):
            return False
        if data.get("transcript_path") and Path(str(data["transcript_path"])).resolve() != original_path.resolve():
            return False
        if data.get("text_sha256") and data["text_sha256"] != original.text_sha256:
            return False
        if data.get("char_count") is not None and data["char_count"] != original.char_count:
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def audit_workspace(settings: Settings) -> dict[str, Any]:
    """Return JSON-serializable findings without opening the database for writing."""
    root = settings.workspace_root
    issues: list[dict[str, object]] = []
    metadata_files = sorted(settings.metadata_dir.glob("*.json")) if settings.metadata_dir.is_dir() else []
    original_files = sorted(settings.transcripts_original_dir.glob("*.txt")) if settings.transcripts_original_dir.is_dir() else []
    metadata_by_path: dict[str, dict[str, Any]] = {}
    transcript_owners: dict[str, list[str]] = defaultdict(list)
    for metadata_path in metadata_files:
        filename = str(metadata_path.resolve())
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("metadata must be a JSON object")
        except (OSError, ValueError) as error:
            _issue(issues, "invalid_metadata", path=str(metadata_path), detail=str(error))
            continue
        metadata_by_path[filename] = data
        if _metadata_source(data) is None:
            _issue(issues, "missing_or_invalid_source", path=str(metadata_path))
        declared = data.get("transcript_path")
        transcript_path = Path(str(declared)) if declared else settings.transcripts_original_dir / f"{metadata_path.stem}.txt"
        transcript_owners[str(transcript_path.resolve())].append(str(metadata_path))
        if transcript_path.stem != metadata_path.stem:
            _issue(issues, "filename_mismatch", metadata=str(metadata_path), transcript=str(transcript_path))
        if not transcript_path.is_file():
            _issue(issues, "missing_transcript", metadata=str(metadata_path), transcript=str(transcript_path))
            continue
        try:
            original_text = transcript_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            _issue(issues, "unreadable_transcript", path=str(transcript_path), detail=str(error))
            continue
        if data.get("text_sha256") and hashlib.sha256(original_text.encode("utf-8")).hexdigest() != data["text_sha256"]:
            _issue(issues, "metadata_hash_mismatch", path=str(metadata_path))
        if data.get("char_count") is not None and len(original_text) != data["char_count"]:
            _issue(issues, "metadata_length_mismatch", path=str(metadata_path))
    for transcript_path in original_files:
        if str(transcript_path.resolve()) not in transcript_owners:
            _issue(issues, "orphan_original_transcript", path=str(transcript_path))
    for transcript, owners in transcript_owners.items():
        if len(owners) > 1:
            _issue(issues, "multiple_metadata_owners", transcript=transcript, metadata=owners)

    counts = {"metadata": len(metadata_files), "original_transcripts": len(original_files),
              "videos": 0, "versions": 0, "tasks": 0}
    if not settings.app_db_path.is_file():
        _issue(issues, "missing_database", path=str(settings.app_db_path))
    else:
        try:
            uri = settings.app_db_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                check = conn.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    _issue(issues, "sqlite_integrity_failure", detail=check[0] if check else "no result")
                for record in conn.execute("PRAGMA foreign_key_check").fetchall():
                    _issue(issues, "foreign_key_failure", table=record[0], rowid=record[1])
                videos = [dict(row) for row in conn.execute("SELECT * FROM videos")]
                versions = [dict(row) for row in conn.execute("SELECT * FROM transcript_versions")]
                tasks = [dict(row) for row in conn.execute("SELECT * FROM tasks")]
                counts.update(videos=len(videos), versions=len(versions), tasks=len(tasks))
                videos_by_id = {int(video["id"]): video for video in videos}
                versions_by_id = {int(item["id"]): item for item in versions}
                versions_by_video: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for version in versions:
                    versions_by_video[int(version["video_id"])].append(version)
                indexed_metadata = set()
                linked_files: dict[str, set[int]] = defaultdict(set)
                for video in videos:
                    video_id = int(video["id"])
                    metadata_path = str(Path(str(video["metadata_path"])).resolve())
                    indexed_metadata.add(metadata_path)
                    data = metadata_by_path.get(metadata_path)
                    if data is None:
                        _issue(issues, "unavailable_video_metadata", video_id=video_id, path=metadata_path)
                    elif not metadata_matches_video(data, video):
                        _issue(issues, "metadata_identity_mismatch", video_id=video_id, path=metadata_path)
                    related = versions_by_video.get(video_id, [])
                    active = [item for item in related if item["is_active"]]
                    if len(active) != 1:
                        _issue(issues, "active_version_count", video_id=video_id, count=len(active))
                    current = versions_by_id.get(int(video["current_transcript_version_id"] or 0))
                    if current is None or int(current["video_id"]) != video_id or not current["is_active"]:
                        _issue(issues, "invalid_current_version", video_id=video_id,
                               version_id=video["current_transcript_version_id"])
                    originals = [item for item in related if item["kind"] == "original"]
                    if len(originals) != 1:
                        _issue(issues, "original_version_count", video_id=video_id, count=len(originals))
                    if data is not None and originals and data.get("transcript_path"):
                        if Path(str(data["transcript_path"])).resolve() != Path(str(originals[0]["file_path"])).resolve():
                            _issue(issues, "original_path_mismatch", video_id=video_id)
                    for item in related:
                        linked_files[str(Path(str(item["file_path"])).resolve())].add(video_id)
                        file = Path(str(item["file_path"]))
                        if not file.is_file():
                            _issue(issues, "missing_version_file", video_id=video_id, version_id=item["id"], path=str(file))
                            continue
                        try:
                            text = file.read_text(encoding="utf-8")
                        except (OSError, UnicodeError) as error:
                            _issue(issues, "unreadable_version", version_id=item["id"], detail=str(error))
                            continue
                        if len(text) != item["char_count"] or hashlib.sha256(text.encode("utf-8")).hexdigest() != item["text_sha256"]:
                            _issue(issues, "version_content_mismatch", video_id=video_id, version_id=item["id"])
                for item in versions:
                    if int(item["video_id"]) not in videos_by_id:
                        _issue(issues, "orphan_version", version_id=item["id"], video_id=item["video_id"])
                for path, owners in linked_files.items():
                    if len(owners) > 1:
                        _issue(issues, "cross_video_file", path=path, video_ids=sorted(owners))
                for metadata_path in metadata_by_path:
                    if metadata_path not in indexed_metadata:
                        _issue(issues, "unindexed_metadata", path=metadata_path)
                for task in tasks:
                    if task["kind"] == "transcription" and task["status"] == "completed":
                        if task["video_id"] is None or int(task["video_id"]) not in videos_by_id:
                            _issue(issues, "completed_task_without_video", task_id=task["id"])
                for row in conn.execute(
                    "SELECT e.id FROM task_progress_events e LEFT JOIN tasks t ON t.id=e.task_id WHERE t.id IS NULL"
                ):
                    _issue(issues, "orphan_progress_event", event_id=row[0])
                for row in conn.execute(
                    "SELECT vt.video_id, vt.tag_id FROM video_tags vt "
                    "LEFT JOIN videos v ON v.id=vt.video_id LEFT JOIN tags t ON t.id=vt.tag_id "
                    "WHERE v.id IS NULL OR t.id IS NULL"
                ):
                    _issue(issues, "orphan_video_tag", video_id=row[0], tag_id=row[1])
                for row in conn.execute(
                    "SELECT v.id FROM videos v LEFT JOIN categories c ON c.id=v.category_id "
                    "WHERE v.category_id IS NOT NULL AND c.id IS NULL"
                ):
                    _issue(issues, "orphan_category", video_id=row[0])
        except (OSError, sqlite3.DatabaseError) as error:
            _issue(issues, "database_read_error", path=str(settings.app_db_path), detail=str(error))
    return {"workspace": str(root), "read_only": True, "counts": counts,
            "issue_count": len(issues), "issues": issues}


def has_verified_completed_source(database: Any, source_input: str, *,
                                  provider: str, model: str,
                                  audio_language: str | None = None) -> bool:
    """Resume eligibility: match source identity, provider/model and on-disk bytes."""
    from b2t.inputs import parse_source

    source = parse_source(source_input)
    expected = _normal_source(source.kind, source.raw_input, source.bv, source.page, source.path)
    if expected is None:
        return False
    for video in database.list_videos():
        if _record_source(video) != expected:
            continue
        if video["engine"] == "bilibili-subtitles":
            if source.kind != "bilibili":
                continue
        elif video["engine"] != provider or video["model"] != model:
            continue
        elif (audio_language and audio_language != "auto"
              and video.get("language") != audio_language):
            continue
        if verified_active_transcript(database, video):
            return True
    return False
