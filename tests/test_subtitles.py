import json
import sys
from types import SimpleNamespace

from typer.testing import CliRunner

from b2t.cli import app
from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.downloaders.ytdlp import YtDlpDownloader
from b2t.factory import build_pipeline
from b2t.inputs import parse_source
from b2t.library import WorkspaceLibrary
from b2t.models import SubtitleResult
from b2t.subtitles import srt_to_text
from b2t.user_config import AppConfig


SRT = "1\n00:00:01,250 --> 00:00:03,000\n<b>第一句</b>\n\n2\n00:00:03,000 --> 00:00:05,000\n第二句 &amp; 内容\n"


def test_srt_text_preserves_multiline_and_repeated_speech():
    assert srt_to_text(SRT) == "第一句\n第二句 & 内容"
    assert srt_to_text("<xml>弹幕</xml>") == ""
    assert srt_to_text("1\n00:00:00,000 --> 00:00:01,000\n42\n42\n") == "42\n42"


def test_subtitle_fetch_uses_metadata_only_and_prefers_chinese(tmp_path, monkeypatch):
    calls = []

    class YoutubeDL:
        def __init__(self, options):
            assert options["skip_download"] is True
            assert options["writesubtitles"] is True
            assert "-danmaku" in options["subtitleslangs"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, *, download):
            calls.append((url, download))
            return {"title": "字幕视频", "subtitles": {
                "danmaku": [{"ext": "xml", "data": "<xml>弹幕</xml>"}],
                "en": [{"ext": "srt", "data": SRT.replace("第一句", "English")}],
                "ai-zh": [{"ext": "srt", "data": SRT.replace("第一句", "AI")}],
                "zh-CN": [{"ext": "srt", "data": SRT}],
            }}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    settings = Settings.from_workspace(tmp_path / ".b2t")
    result = YtDlpDownloader().fetch_subtitle(parse_source("BV1xx411c7XD"), settings)
    assert result.text == "第一句\n第二句 & 内容"
    assert result.language == "zh-CN"
    assert calls == [("https://www.bilibili.com/video/BV1xx411c7XD", False)]
    assert not settings.downloads_dir.exists()


def test_subtitle_fetch_does_not_treat_malformed_subtitles_as_missing(tmp_path, monkeypatch):
    import pytest
    from b2t.subtitles import SubtitleLookupError
    class YoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            return {"subtitles": {"danmaku": [{"ext": "xml", "data": "<xml/>"}],
                                  "zh-CN": [{"ext": "srt", "data": "invalid"}]}}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    with pytest.raises(SubtitleLookupError, match="could not be read"):
        YtDlpDownloader().fetch_subtitle(parse_source("BV1xx411c7XD"), Settings.from_workspace(tmp_path))


def test_login_required_subtitles_do_not_fall_back_to_video_download(tmp_path, monkeypatch):
    """Login-only captions are not evidence that this video has no captions."""
    from b2t.pipeline import B2TPipeline
    import pytest

    class YoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            if not self.options.get("no_warnings"):
                self.options["logger"].warning("Subtitles are only available when logged in. Use cookies for authentication.")
            return {"subtitles": {"danmaku": [{"ext": "xml", "data": "<xml/>"}]}}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    downloaded = []
    monkeypatch.setattr(YtDlpDownloader, "download", lambda *a, **kw: downloaded.append(True))
    pipeline = B2TPipeline(settings=Settings.from_workspace(tmp_path / ".b2t"), downloader=YtDlpDownloader())
    with pytest.raises(RuntimeError, match="cookies"):
        pipeline.transcribe("BV19Eaa6wESG")
    assert downloaded == []


def test_subtitle_network_failure_does_not_download_media_or_expose_url(tmp_path, monkeypatch):
    from b2t.pipeline import B2TPipeline
    from b2t.subtitles import SubtitleLookupError
    import pytest

    class YoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            raise ConnectionError("https://example.test/subtitle?secret=token")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    monkeypatch.setattr(YtDlpDownloader, "download", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not download media")))
    pipeline = B2TPipeline(settings=Settings.from_workspace(tmp_path / ".b2t"), downloader=YtDlpDownloader())
    with pytest.raises(SubtitleLookupError, match="ConnectionError") as error:
        pipeline.transcribe("BV19Eaa6wESG")
    assert "secret" not in str(error.value)


def test_danmaku_only_without_login_warning_confirms_no_subtitles(tmp_path, monkeypatch):
    class YoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            return {"subtitles": {"danmaku": [{"ext": "xml", "data": "<xml/>"}]}}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    assert YtDlpDownloader().fetch_subtitle(parse_source("BV1xx411c7XD"), Settings.from_workspace(tmp_path)) is None


def test_pipeline_saves_subtitles_without_building_transcriber_or_media(tmp_path, monkeypatch):
    settings = Settings.from_workspace(tmp_path / ".b2t")
    monkeypatch.setattr("b2t.factory.LocalWhisperTranscriber", lambda **kw: (_ for _ in ()).throw(AssertionError("must not build transcriber")))
    monkeypatch.setattr(YtDlpDownloader, "download", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not download media")))
    monkeypatch.setattr(YtDlpDownloader, "fetch_subtitle", lambda *a, **kw: SubtitleResult(
        text=srt_to_text(SRT), content=SRT, language="zh-CN", title="Subtitle title",
        metadata={"title": "Subtitle title"},
    ))
    result = build_pipeline(settings=settings, config=AppConfig()).transcribe("https://www.bilibili.com/video/BV1xx411c7XD?p=2")
    assert result.audio_path is None and result.video_path is None
    assert result.engine == "bilibili-subtitles"
    assert result.transcript_path.with_suffix(".srt").read_text() == SRT
    metadata = json.loads(result.metadata_path.read_text())
    assert metadata["source"]["page"] == 2
    assert metadata["audio_path"] is None
    assert list(settings.audio_dir.iterdir()) == []
    assert list(settings.downloads_dir.iterdir()) == []
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    video_id = library.register_transcript_result(result)
    assert database.get_video(video_id)["audio_path"] == ""
    assert library.load_video_metadata(video_id)["audio_path"] is None


def test_download_transcribe_subtitles_skip_media_and_reuse_saved_result(tmp_path, monkeypatch):
    settings = Settings.from_workspace(tmp_path / ".b2t")
    calls = []

    def fetch(self, source, settings, **kwargs):
        calls.append(source.raw_input)
        return SubtitleResult(text=srt_to_text(SRT), content=SRT, language="zh-CN")

    monkeypatch.setattr(YtDlpDownloader, "fetch_subtitle", fetch)
    monkeypatch.setattr(YtDlpDownloader, "download", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not download media")))
    monkeypatch.setattr("b2t.factory.LocalWhisperTranscriber", lambda **kw: (_ for _ in ()).throw(AssertionError("must not build transcriber")))
    arguments = ["download", "BV1xx411c7XD", "--transcribe", "--workspace", str(settings.workspace_root)]
    runner = CliRunner()
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert "跳过视频下载与语音转写" in result.stdout
    result = runner.invoke(app, arguments + ["--audio-language", "zh", "--model", "medium"])
    assert result.exit_code == 0, result.output
    assert "跳过已有文字稿 1 个" in result.stdout
    assert calls == ["BV1xx411c7XD"]
    assert list(settings.downloads_dir.iterdir()) == []
    assert list(settings.audio_dir.iterdir()) == []
    assert len(AppDatabase(settings).list_tasks()) == 1
    # Another part of the same BV must not be mistaken for this subtitle.
    result = runner.invoke(app, ["download", "https://www.bilibili.com/video/BV1xx411c7XD?p=2", "--transcribe", "--workspace", str(settings.workspace_root)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert len(list(settings.transcripts_original_dir.glob("*.txt"))) == 2
    assert len(AppDatabase(settings).list_videos()) == 2
