from pathlib import Path

from b2t.config import Settings
from b2t.downloaders.base import Downloader
from b2t.models import DownloadResult, SourceRef
from b2t.pipeline import B2TPipeline, _parse_ffmpeg_progress_seconds
from b2t.transcribers.base import Transcriber


class FakeDownloader(Downloader):
    name = "fake"

    def __init__(self, video_path: Path) -> None:
        self.video_path = video_path

    def download(self, source: SourceRef, settings: Settings, *, progress=None) -> DownloadResult:
        return DownloadResult(
            source=source,
            video_path=self.video_path,
            title="demo-title",
            metadata={"title": "demo-title"},
        )


class FakeTranscriber(Transcriber):
    name = "fake-whisper"

    def transcribe(self, audio_path: Path, *, prompt: str | None = None, progress=None) -> dict[str, str]:
        assert audio_path.exists()
        return {
            "text": "hello from b2t",
            "language": "zh",
            "model": "small",
        }


class PipelineUnderTest(B2TPipeline):
    def _extract_audio(self, video_path: Path, stem: str, progress=None) -> Path:
        audio_path = self.settings.audio_dir / f"{stem}.wav"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"wav")
        return audio_path


def test_pipeline_transcribes_bilibili_source(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    pipeline = PipelineUnderTest(
        settings=settings,
        downloader=FakeDownloader(video_path),
        transcriber=FakeTranscriber(),
    )

    result = pipeline.transcribe("BV1xx411c7XD")
    assert result.text == "hello from b2t"
    assert result.transcript_path.exists()
    assert result.metadata_path.exists()
    assert result.video_path == video_path


def test_pipeline_respects_custom_output_file(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"wav")
    output_path = tmp_path / "custom-result"

    pipeline = PipelineUnderTest(
        settings=settings,
        downloader=FakeDownloader(tmp_path / "unused.mp4"),
        transcriber=FakeTranscriber(),
    )

    result = pipeline.transcribe(str(audio_path), output=output_path)
    assert result.transcript_path == output_path.with_suffix(".txt")
    assert result.transcript_path.exists()


def test_parse_ffmpeg_progress_seconds_supports_us_and_ms() -> None:
    assert _parse_ffmpeg_progress_seconds("out_time_ms=2500000") == 2.5
    assert _parse_ffmpeg_progress_seconds("out_time_us=4000000") == 4.0
    assert _parse_ffmpeg_progress_seconds("progress=continue") is None


def test_pipeline_reuses_prepared_download_and_preserves_bv(tmp_path, monkeypatch):
    from b2t.inputs import parse_source

    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    video = settings.downloads_dir / "BV1xx411c7XD.mp4"
    video.write_bytes(b"video")
    downloader = FakeDownloader(video)
    monkeypatch.setattr(downloader, "download", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must reuse video")))
    prepared = DownloadResult(parse_source("BV1xx411c7XD"), video, title="Existing title", metadata={"title": "Existing title"})
    pipeline = PipelineUnderTest(settings=settings, downloader=downloader, transcriber=FakeTranscriber())
    result = pipeline.transcribe("BV1xx411c7XD", downloaded=prepared)
    assert result.source.bv == "BV1xx411c7XD"
    assert result.metadata["download"]["title"] == "Existing title"
    assert result.video_path == video


def test_pipeline_rejects_prepared_download_for_another_video(tmp_path):
    import pytest
    from b2t.inputs import parse_source

    settings = Settings.from_workspace(tmp_path / ".b2t")
    video = tmp_path / "video.mp4"
    pipeline = PipelineUnderTest(settings=settings, downloader=FakeDownloader(video), transcriber=FakeTranscriber())
    with pytest.raises(ValueError, match="does not match"):
        pipeline.transcribe("BV1xx411c7XD", downloaded=DownloadResult(parse_source("BV1G8uU6nECD"), video))


def test_pipeline_saves_whisper_timing_and_multiline_text(tmp_path):
    import json

    class WhisperWithTiming(FakeTranscriber):
        name = "whisper"

        def transcribe(self, audio_path, **kwargs):
            return {"text": "第一句第二句。第三句？", "model": "small", "language": "zh",
                    "segments": [{"start": 0, "end": 2, "text": "第一句", "tokens": [1, 2]},
                                 {"start": 2, "end": 5, "text": "第二句。第三句？"}]}

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    pipeline = B2TPipeline(settings=Settings.from_workspace(tmp_path / ".b2t"),
                           downloader=FakeDownloader(tmp_path / "unused.mp4"),
                           transcriber=WhisperWithTiming())
    result = pipeline.transcribe(str(audio))
    assert result.transcript_path.read_text() == "第一句第二句。\n第三句？\n"
    saved = json.loads(result.metadata_path.read_text())
    assert saved["segments"] == [{"start": 0.0, "end": 2.0, "text": "第一句"},
                                 {"start": 2.0, "end": 5.0, "text": "第二句。第三句？"}]


def test_pipeline_simplifies_output_but_preserves_recognition_and_settings(tmp_path):
    import json

    class WhisperWithSettings(FakeTranscriber):
        name = "whisper"

        def transcribe(self, audio_path, **kwargs):
            return {"text": "這是第一句。這是第二句。", "model": "small", "language": "zh", "device": "cpu",
                    "initial_prompt": "以下是普通话的句子。",
                    "segments": [{"start": 0, "end": 2, "text": "這是第一句。"},
                                 {"start": 2, "end": 5, "text": "這是第二句。"}]}

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    pipeline = B2TPipeline(settings=Settings.from_workspace(tmp_path / ".b2t"),
                           downloader=FakeDownloader(tmp_path / "unused.mp4"), transcriber=WhisperWithSettings())
    result = pipeline.transcribe(str(audio))
    assert result.text == "这是第一句。\n这是第二句。"
    saved = json.loads(result.metadata_path.read_text())
    assert saved["text"] == "這是第一句。這是第二句。"
    assert saved["segments"][0]["text"] == "這是第一句。"
    assert saved["output_script"] == "simplified"
    assert saved["device"] == "cpu"
    assert saved["initial_prompt"] == "以下是普通话的句子。"
    pipeline.simplify_whisper = False
    assert pipeline.transcribe(str(audio)).text == "這是第一句。\n這是第二句。"
