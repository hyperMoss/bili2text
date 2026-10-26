from pathlib import Path

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.library import WorkspaceLibrary
from b2t.models import SourceRef, TranscriptResult
from b2t.tasks import TaskService


class FakePipeline:
    def __init__(self, settings: Settings, provider: str, model: str) -> None:
        self.settings = settings
        self.provider = provider
        self.model = model

    def transcribe(self, source: str, *, prompt: str | None = None, output: Path | None = None, progress=None) -> TranscriptResult:
        if progress is not None:
            progress.running("transcribing", message="transcribing", stage_progress=0.5)
        transcript_path = self.settings.transcripts_original_dir / "demo-task.txt"
        transcript_path.write_text("task text\n", encoding="utf-8")
        metadata_path = self.settings.metadata_dir / "demo-task.json"
        metadata_path.write_text("{}", encoding="utf-8")
        return TranscriptResult(
            source=SourceRef(raw_input=source, kind="bilibili", display_name="demo", url=source, bv="BV1xx411c7XD"),
            engine=self.provider,
            model=self.model,
            text="task text",
            audio_path=self.settings.audio_dir / "demo-task.wav",
            transcript_path=transcript_path,
            metadata_path=metadata_path,
            video_path=self.settings.downloads_dir / "demo-task.mp4",
            metadata={"language": "zh", "download": {"title": "Demo Task"}},
        )


def test_task_service_runs_background_transcription_and_indexes_result(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    service = TaskService(
        database=database,
        library=library,
        pipeline_factory=lambda provider, model: FakePipeline(settings, provider, model),
    )

    task = service.submit_transcription(
        source="https://www.bilibili.com/video/BV1xx411c7XD",
        provider="whisper",
        model="small",
    )
    completed = service.wait_for_task(task.id)

    assert completed.status == "completed"
    assert completed.video_id is not None
    videos = database.list_videos()
    assert len(videos) == 1
    assert videos[0]["title"] == "Demo Task"


def test_whisper_tasks_queue_without_blocking_other_providers(tmp_path: Path) -> None:
    from threading import Event

    first_started, release_first, cloud_started = (Event() for _ in range(3))
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)

    class BlockingPipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            if source == "first":
                first_started.set()
                assert release_first.wait(5)
            elif self.provider == "volcengine":
                cloud_started.set()
            return super().transcribe(source, **kwargs)

    service = TaskService(
        database=database,
        library=WorkspaceLibrary(settings, database),
        pipeline_factory=lambda provider, model: BlockingPipeline(settings, provider, model),
    )
    try:
        first = service.submit_transcription(source="first", provider="whisper", model="small")
        assert first_started.wait(5)
        second = service.submit_transcription(source="second", provider="whisper", model="small")
        cloud = service.submit_transcription(source="cloud", provider="volcengine", model="flash")
        assert cloud_started.wait(5)
        assert service.wait_for_task(cloud.id).status == "completed"
        assert service.get_task(second.id).status == "queued"
    finally:
        release_first.set()
        service.wait_for_task(first.id)
        service.wait_for_task(second.id)
        service.shutdown(wait=True)
