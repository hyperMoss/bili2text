from pathlib import Path

from typer.testing import CliRunner

from b2t.cleanup import clear_downloads, plan_download_cleanup
from b2t.cli import app
from b2t.config import Settings
from b2t.database import AppDatabase


runner = CliRunner()


def test_download_cleanup_plan_is_read_only_and_clear_preserves_root(tmp_path: Path) -> None:
    downloads = tmp_path / ".b2t" / "downloads"
    first = downloads / "first.mp4"
    imported = downloads / "imports" / "batch" / "second.mp4"
    first.parent.mkdir(parents=True)
    imported.parent.mkdir(parents=True)
    first.write_bytes(b"1234")
    imported.write_bytes(b"56")

    plan = plan_download_cleanup(downloads)

    assert plan.file_count == 2
    assert plan.total_bytes == 6
    assert first.exists()
    assert imported.exists()

    removed = clear_downloads(plan)

    assert removed == 2
    assert downloads.is_dir()
    assert list(downloads.iterdir()) == []


def test_clean_downloads_command_requires_explicit_confirmation(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.downloads_dir.mkdir(parents=True)
    media = settings.downloads_dir / "video.mp4"
    media.write_bytes(b"video")

    preview = runner.invoke(app, ["clean-downloads", "--workspace", str(settings.workspace_root)])

    assert preview.exit_code == 0, preview.output
    assert "预览" in preview.stdout
    assert media.exists()

    cleared = runner.invoke(app, ["clean-downloads", "--yes", "--workspace", str(settings.workspace_root)])

    assert cleared.exit_code == 0, cleared.output
    assert "已清理" in cleared.stdout
    assert not media.exists()


def test_clean_downloads_command_keeps_media_while_a_task_is_active(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.downloads_dir.mkdir(parents=True)
    media = settings.downloads_dir / "video.mp4"
    media.write_bytes(b"video")
    AppDatabase(settings).create_task(
        kind="transcription",
        source_input="video.mp4",
        provider="whisper",
        model="small",
    )

    result = runner.invoke(app, ["clean-downloads", "--yes", "--workspace", str(settings.workspace_root)])

    assert result.exit_code == 1
    assert "不能清理" in result.stderr
    assert media.exists()
