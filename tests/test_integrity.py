import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.integrity import audit_workspace, verified_active_transcript, has_verified_completed_source
from b2t.library import WorkspaceLibrary
from b2t.models import SourceRef, TranscriptResult


def make_result(settings: Settings, name: str, bv: str) -> TranscriptResult:
    transcript = settings.transcripts_original_dir / f"{name}.txt"
    transcript.write_text("recorded text\n", encoding="utf-8")
    return TranscriptResult(
        source=SourceRef(raw_input=bv, kind="bilibili", display_name=name, bv=bv),
        engine="whisper", model="small", text="recorded text",
        transcript_path=transcript, metadata_path=settings.metadata_dir / f"{name}.json",
        audio_path=None, metadata={"source": {"kind": "bilibili", "raw_input": bv, "bv": bv},
                                   "engine": "whisper", "model": "small", "language": "zh"},
    )


def test_database_rejects_cross_video_version_activation(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    first = library.register_transcript_result(make_result(settings, "first", "BV1xx411c7XD"))
    second = library.register_transcript_result(make_result(settings, "second", "BV1G8uU6nECD"))
    version = database.get_active_transcript_version(second)
    original = database.get_active_transcript_version(first)
    with pytest.raises(ValueError, match="does not belong"):
        database.activate_transcript_version(first, version.id)
    assert database.get_active_transcript_version(first).id == original.id
    assert database.get_active_transcript_version(second).id == version.id


def test_duplicate_registration_is_safe_and_does_not_duplicate_versions(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    result = make_result(settings, "example", "BV1xx411c7XD")
    video_id = library.register_transcript_result(result)
    assert library.register_transcript_result(result) == video_id
    assert len(database.list_transcript_versions(video_id)) == 1
    assert audit_workspace(settings)["issue_count"] == 0


def test_unsafe_metadata_collision_is_rejected(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    first = make_result(settings, "shared", "BV1xx411c7XD")
    library.register_transcript_result(first)
    conflicting = make_result(settings, "shared", "BV1G8uU6nECD")
    with pytest.raises(ValueError, match="cannot safely reuse metadata"):
        library.register_transcript_result(conflicting)
    assert len(database.list_videos()) == 1


def test_audit_is_read_only_and_reports_corruption(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    video = library.register_transcript_result(make_result(settings, "entry", "BV1xx411c7XD"))
    before = settings.app_db_path.stat().st_mtime_ns
    assert audit_workspace(settings)["issue_count"] == 0
    assert verified_active_transcript(database, database.get_video(video))
    version = database.get_active_transcript_version(video)
    Path(version.file_path).write_text("modified\n", encoding="utf-8")
    report = audit_workspace(settings)
    assert report["read_only"]
    assert "version_content_mismatch" in [item["code"] for item in report["issues"]]
    assert not verified_active_transcript(database, database.get_video(video))
    assert settings.app_db_path.stat().st_mtime_ns == before


def test_audit_missing_database_does_not_create_workspace(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".missing")
    report = audit_workspace(settings)
    assert "missing_database" in [item["code"] for item in report["issues"]]
    assert not settings.workspace_root.exists()


def test_metadata_wrong_part_is_not_accepted(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    result = make_result(settings, "pages", "BV1xx411c7XD")
    video = library.register_transcript_result(result)
    metadata_path = settings.metadata_dir / "pages.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["source"]["page"] = 2
    metadata_path.write_text(json.dumps(data), encoding="utf-8")
    report = audit_workspace(settings)
    assert "metadata_identity_mismatch" in [item["code"] for item in report["issues"]]
    assert not verified_active_transcript(database, database.get_video(video))


def test_duplicate_path_with_different_source_fails_before_sqlite_insert(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    video = library.register_transcript_result(make_result(settings, "name", "BV1xx411c7XD"))
    with pytest.raises(ValueError, match="metadata path collision"):
        database.create_video(source_kind="bilibili", source_input="BV1G8uU6nECD",
                              source_url=None, source_bv="BV1G8uU6nECD", title="Other",
                              display_name="name", language="zh", engine="whisper",
                              model="small", video_path=None, audio_path="",
                              metadata_path=str(settings.metadata_dir / "name.json"))
    assert database.get_video(video)["source_bv"] == "BV1xx411c7XD"


def test_resume_requires_valid_metadata_and_hash(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    video_id = library.register_transcript_result(make_result(settings, "resume", "BV1xx411c7XD"))
    assert has_verified_completed_source(
        database, "BV1xx411c7XD", provider="whisper", model="small", audio_language="zh"
    )
    assert not has_verified_completed_source(
        database, "BV1xx411c7XD", provider="whisper", model="large"
    )
    assert not has_verified_completed_source(
        database, "https://www.bilibili.com/video/BV1xx411c7XD?p=2", provider="whisper", model="small"
    )
    original = database.get_active_transcript_version(video_id)
    Path(original.file_path).write_text("corrupt\n", encoding="utf-8")
    assert not has_verified_completed_source(
        database, "BV1xx411c7XD", provider="whisper", model="small"
    )
