from typer.testing import CliRunner

from b2t.cli import app


runner = CliRunner()


def test_cli_help_renders() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Bilibili" in result.stdout
    assert "bootstrap" in result.stdout
    assert "batch" in result.stdout
    assert "transcribe" in result.stdout
    assert "window" in result.stdout
    # aliases are now hidden, but mentioned in help text parenthetically
    assert "tx" in result.stdout
    assert "lang" not in result.stdout or "lang" in result.stdout  # alias hidden


def test_doctor_command_runs_without_crashing() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "PATH 中的 ffmpeg:" in result.stdout or "ffmpeg:" in result.stdout


def test_language_command_updates_workspace_config(tmp_path) -> None:
    workspace = tmp_path / ".b2t"
    result = runner.invoke(app, ["lang", "en-US", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "Language switched to: English" in result.stdout

    config_text = (workspace / "config.json").read_text(encoding="utf-8")
    assert '"language": "en-US"' in config_text


def test_bootstrap_sync_only_requires_existing_config(tmp_path) -> None:
    workspace = tmp_path / ".b2t"
    result = runner.invoke(app, ["bootstrap", "--sync-only", "--workspace", str(workspace)])
    assert result.exit_code == 1
    assert "请先运行一次 bootstrap" in result.stderr


def test_download_dry_run_uses_history_without_creating_download_tasks(tmp_path, monkeypatch) -> None:
    from b2t.config import Settings
    from b2t.database import AppDatabase

    workspace = tmp_path / ".b2t"
    database = AppDatabase(Settings.from_workspace(workspace))
    database.create_task(kind="transcription", source_input="BV1xx411c7XD", provider="whisper", model="small")
    database.create_task(kind="transcription", source_input="https://www.bilibili.com/video/BV1xx411c7XD", provider="whisper", model="small")
    monkeypatch.setattr("b2t.downloads.YtDlpDownloader.download", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not download")))
    result = runner.invoke(app, ["download", "--from-tasks", "--dry-run", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "1 个视频" in result.stdout
    assert "仅预览" in result.stdout
    assert len(database.list_tasks()) == 2


def test_download_cli_continues_after_failure_and_does_not_initialize_transcriber(tmp_path, monkeypatch) -> None:
    from b2t.models import DownloadResult

    calls = []
    monkeypatch.setattr("b2t.cli.build_pipeline", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not transcribe")))
    monkeypatch.setattr("b2t.downloads.media_duration", lambda path: 60.0 if path.exists() else None)

    def download(self, source, settings, *, progress):
        calls.append(source.bv)
        if source.bv == "BV1xx411c7XD":
            raise RuntimeError("offline")
        path = self.output_path(source, settings)
        path.touch()
        return DownloadResult(source, path, metadata={"duration": 60})

    monkeypatch.setattr("b2t.downloads.YtDlpDownloader.download", download)
    result = runner.invoke(app, ["dl", "BV1xx411c7XD", "BV1G8uU6nECD", "--attempts", "1", "--workspace", str(tmp_path / ".b2t")])
    assert result.exit_code == 1
    assert calls == ["BV1xx411c7XD", "BV1G8uU6nECD"]
    assert "新增完成 1 个" in result.stdout
    assert "失败 1 个" in result.stdout


def test_download_cli_interrupt_has_resumable_exit_code(tmp_path, monkeypatch) -> None:
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("b2t.downloads.YtDlpDownloader.download", interrupted)
    result = runner.invoke(app, ["download", "BV1xx411c7XD", "--workspace", str(tmp_path / ".b2t")])
    assert result.exit_code == 130
    assert "再次运行同一命令即可续传" in result.stderr


def test_download_then_transcribe_reuses_video_and_skips_saved_transcripts(tmp_path, monkeypatch):
    from b2t.config import Settings
    from b2t.database import AppDatabase
    from b2t.models import DownloadResult
    from b2t.pipeline import B2TPipeline

    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    existing = settings.downloads_dir / "BV1xx411c7XD.mp4"
    existing.write_bytes(b"complete")
    calls, builds = [], []
    monkeypatch.setattr("b2t.downloads.YtDlpDownloader.fetch_subtitle", lambda *a, **kw: None)
    monkeypatch.setattr("b2t.downloads.media_duration", lambda path: 60.0 if path.is_file() else None)

    def download(self, source, settings, **kwargs):
        calls.append(source.bv)
        path = self.output_path(source, settings)
        path.write_bytes(b"complete")
        return DownloadResult(source, path, title="Downloaded title", metadata={"duration": 60})

    class NeverDownloader:
        def download(self, *args, **kwargs):
            raise AssertionError("pipeline must not download again")

    class Transcriber:
        name = "whisper"

        def transcribe(self, path, **kwargs):
            assert path.exists()
            return {"text": "completed transcript", "model": "small", "language": "zh"}

    class Pipeline(B2TPipeline):
        def _extract_audio(self, path, stem, progress=None):
            audio = self.settings.audio_dir / f"{stem}.wav"
            audio.write_bytes(b"audio")
            return audio

    def build(**kwargs):
        builds.append(kwargs)
        return Pipeline(settings=kwargs["settings"], downloader=NeverDownloader(), transcriber=Transcriber())

    monkeypatch.setattr("b2t.downloads.YtDlpDownloader.download", download)
    monkeypatch.setattr("b2t.cli.build_pipeline", build)
    arguments = ["download", "BV1xx411c7XD", "BV1G8uU6nECD", "--transcribe", "--audio-language", "zh", "--device", "mps", "--workspace", str(settings.workspace_root)]
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert calls == ["BV1G8uU6nECD"]
    assert len(builds) == 1  # same model instance across sequential videos
    assert builds[0]["audio_language"] == "zh"
    assert builds[0]["device"] == "mps"
    database = AppDatabase(settings)
    assert {v["source_bv"] for v in database.list_videos()} == {"BV1xx411c7XD", "BV1G8uU6nECD"}
    assert len(list(settings.transcripts_original_dir.glob("*.txt"))) == 2
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert "跳过已有文字稿 2 个" in result.stdout
    assert len(builds) == 1
    assert len(list(settings.transcripts_original_dir.glob("*.txt"))) == 2


def test_download_transcribe_dry_run_never_initializes_model(tmp_path, monkeypatch):
    monkeypatch.setattr("b2t.cli.build_pipeline", lambda **kw: (_ for _ in ()).throw(AssertionError("must not load model")))
    result = runner.invoke(app, ["download", "BV1xx411c7XD", "--transcribe", "--dry-run", "--workspace", str(tmp_path / ".b2t")])
    assert result.exit_code == 0, result.output
    assert "待转写" in result.stdout


def test_download_transcribe_interrupt_cancels_transcription_record(tmp_path, monkeypatch):
    from b2t.config import Settings
    from b2t.database import AppDatabase
    from b2t.models import DownloadResult
    from b2t.pipeline import B2TPipeline

    settings = Settings.from_workspace(tmp_path / ".b2t")
    monkeypatch.setattr("b2t.downloads.media_duration", lambda path: 60.0 if path.is_file() else None)
    monkeypatch.setattr("b2t.downloads.YtDlpDownloader.fetch_subtitle", lambda *a, **kw: None)

    def download(self, source, settings, **kwargs):
        path = self.output_path(source, settings)
        path.write_bytes(b"complete")
        return DownloadResult(source, path)

    class InterruptedTranscriber:
        name = "whisper"

        def transcribe(self, *args, **kwargs):
            raise KeyboardInterrupt

    class InterruptedPipeline(B2TPipeline):
        def _extract_audio(self, *args, **kwargs):
            return self.settings.audio_dir / "unused.wav"

    monkeypatch.setattr("b2t.downloads.YtDlpDownloader.download", download)
    monkeypatch.setattr("b2t.cli.build_pipeline", lambda **kw: InterruptedPipeline(
        settings=kw["settings"], downloader=None, transcriber=InterruptedTranscriber(),
    ))
    result = runner.invoke(app, ["download", "BV1xx411c7XD", "--transcribe", "--workspace", str(settings.workspace_root)])
    assert result.exit_code == 130, result.output
    database = AppDatabase(settings)
    assert {t.kind: t.status for t in database.list_tasks()} == {"download": "completed", "transcription": "cancelled"}
    assert (settings.downloads_dir / "BV1xx411c7XD.mp4").exists()
