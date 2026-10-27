import json
from pathlib import Path

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.library import WorkspaceLibrary
from b2t.models import ProgressSnapshot


def test_settings_create_database_and_workspace_directories(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()

    assert settings.transcripts_original_dir.exists()
    assert settings.transcripts_edited_dir.exists()
    assert settings.tasks_dir.exists()


def test_database_persists_task_progress(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)

    task = database.create_task(
        kind="transcription",
        source_input="BV1xx411c7XD",
        provider="whisper",
        model="small",
    )
    database.record_progress(
        ProgressSnapshot(
            task_id=task.id,
            status="running",
            stage="downloading",
            message="downloading",
            percent=0.25,
        )
    )

    loaded = database.get_task(task.id)
    assert loaded is not None
    assert loaded.current_stage == "downloading"
    assert loaded.progress_percent == 0.25


def test_database_cancels_tasks_left_by_a_stopped_runner(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    stopped_task = database.create_task(
        kind="transcription",
        source_input="stopped.mp4",
        provider="whisper",
        model="small",
        runner_pid=999_999_999,
    )
    database.record_progress(
        ProgressSnapshot(
            task_id=stopped_task.id,
            status="running",
            stage="transcribing",
            message="transcribing",
            percent=0.5,
        )
    )
    active_task = database.create_task(
        kind="transcription",
        source_input="active.mp4",
        provider="whisper",
        model="small",
    )

    assert database.reconcile_interrupted_tasks() == 1

    stopped = database.get_task(stopped_task.id)
    assert stopped is not None
    assert stopped.status == "cancelled"
    assert stopped.current_stage == "cancelled"
    assert stopped.finished_at is not None
    assert "stopped" in stopped.current_message
    assert database.get_task(active_task.id).status == "queued"


def test_workspace_library_indexes_existing_files(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    transcript_path = settings.transcripts_original_dir / "demo-1.txt"
    metadata_path = settings.metadata_dir / "demo-1.json"
    transcript_path.write_text("hello world\n", encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "source": {
                    "raw_input": "BV1xx411c7XD",
                    "kind": "bilibili",
                    "url": "https://www.bilibili.com/video/BV1xx411c7XD",
                    "bv": "BV1xx411c7XD",
                },
                "engine": "whisper",
                "model": "small",
                "audio_path": str(settings.audio_dir / "demo.wav"),
                "video_path": str(settings.downloads_dir / "demo.mp4"),
                "download": {"title": "Demo Title"},
                "language": "zh",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    library.index_existing_workspace()

    videos = database.list_videos()
    assert len(videos) == 1
    assert videos[0]["title"] == "Demo Title"
    active = database.get_active_transcript_version(int(videos[0]["id"]))
    assert active is not None
    assert active.file_path == str(transcript_path)
