from pathlib import Path

import pytest

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.downloads import DownloadBatchService, media_duration
from b2t.models import DownloadResult


@pytest.fixture
def service(tmp_path, monkeypatch):
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    monkeypatch.setattr("b2t.downloads.media_duration", lambda path: 60.0 if path.is_file() and path.read_bytes() == b"complete" else None)
    monkeypatch.setattr("b2t.downloads.time.sleep", lambda seconds: None)
    return DownloadBatchService(settings, database)


def test_history_plan_deduplicates_bv_urls_and_preserves_distinct_parts(service):
    for source in ["BV1xx411c7XD", "https://www.bilibili.com/video/BV1xx411c7XD?p=1", "https://www.bilibili.com/video/BV1xx411c7XD?p=2"]:
        service.database.create_task(kind="transcription", source_input=source, provider="whisper", model="small")
    plan = service.plan(["BV1xx411c7XD"], from_tasks=True)
    assert len(plan) == 2
    assert [item.path.name for item in plan] == ["BV1xx411c7XD.mp4", "BV1xx411c7XD.p02.mp4"]
    assert service.database.list_tasks()[0].kind == "transcription"


def test_plan_skips_complete_media_and_keeps_partial_files(service):
    path = service.settings.downloads_dir / "BV1xx411c7XD.mp4"
    path.write_bytes(b"complete")
    assert service.plan(["BV1xx411c7XD"])[0].skip
    path.write_bytes(b"invalid")
    partial = path.with_suffix(".mp4.part")
    partial.write_bytes(b"partial")
    assert not service.plan(["BV1xx411c7XD"])[0].skip
    assert partial.read_bytes() == b"partial"


def test_download_retries_partial_file_and_records_completion(service, monkeypatch):
    item = service.plan(["BV1xx411c7XD"])[0]
    partial = item.path.with_suffix(".mp4.part")
    calls = []

    def download(source, settings, *, progress):
        calls.append(source)
        if len(calls) == 1:
            partial.write_bytes(b"partial")
            raise RuntimeError("connection interrupted")
        assert partial.read_bytes() == b"partial"
        item.path.write_bytes(b"complete")
        return DownloadResult(source, item.path, metadata={"duration": 60})

    monkeypatch.setattr(service.downloader, "download", download)
    result = service.run_one(item)
    assert result.video_path == item.path
    assert len(calls) == 2
    task = service.database.list_tasks()[0]
    assert task.kind == "download"
    assert task.status == "completed"
    assert list(service.settings.transcripts_dir.rglob("*.txt")) == []


def test_interrupt_records_cancelled_and_leaves_partial_for_next_run(service, monkeypatch):
    item = service.plan(["BV1xx411c7XD"])[0]
    partial = item.path.with_suffix(".mp4.part")

    def interrupted(*args, **kwargs):
        partial.write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(service.downloader, "download", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.run_one(item)
    task = service.database.list_tasks()[0]
    assert task.status == "cancelled"
    assert task.finished_at is not None
    assert partial.read_bytes() == b"partial"
    assert not service.plan(["BV1xx411c7XD"], from_tasks=True)[0].skip


def test_failed_download_is_not_recorded_complete(service, monkeypatch):
    item = service.plan(["BV1xx411c7XD"])[0]

    def incomplete(source, settings, **kwargs):
        item.path.write_bytes(b"invalid")
        return DownloadResult(source, item.path)

    monkeypatch.setattr(service.downloader, "download", incomplete)
    with pytest.raises(RuntimeError, match="validation"):
        service.run_one(item)
    assert service.database.list_tasks()[0].status == "failed"


def test_invalid_final_file_is_preserved_so_retry_can_replace_it(service, monkeypatch):
    item = service.plan(["BV1xx411c7XD"])[0]
    item.path.write_bytes(b"invalid")

    def repaired(source, settings, **kwargs):
        assert not item.path.exists()
        item.path.write_bytes(b"complete")
        return DownloadResult(source, item.path, metadata={"duration": 60})

    monkeypatch.setattr(service.downloader, "download", repaired)
    service.run_one(item)
    backups = list(item.path.parent.glob("*.invalid-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"invalid"
    assert item.path.read_bytes() == b"complete"


def test_history_ignores_local_transcription_inputs_but_explicit_local_input_is_rejected(service, tmp_path):
    local = tmp_path / "local.wav"
    local.touch()
    service.database.create_task(kind="transcription", source_input=str(local), provider="whisper", model="small")
    service.database.create_task(kind="transcription", source_input="BV1xx411c7XD", provider="whisper", model="small")
    assert len(service.plan([], from_tasks=True)) == 1
    with pytest.raises(ValueError, match="only accepts"):
        service.plan([str(local)])


def test_media_probe_rejects_files_without_audio_track(tmp_path, monkeypatch):
    from types import SimpleNamespace

    video = tmp_path / "incomplete.mp4"
    video.touch()
    monkeypatch.setattr("b2t.downloads.shutil.which", lambda command: "/usr/bin/ffprobe")
    monkeypatch.setattr("b2t.downloads.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"format":{"duration":"60"},"streams":[{"codec_type":"video"}]}'))
    assert media_duration(video) is None
