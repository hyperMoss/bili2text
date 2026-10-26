from pathlib import Path
import runpy
import sys

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.progress import ProgressReporter


def test_monitor_displays_latest_task_progress_without_submitting_tasks(tmp_path, monkeypatch, capsys):
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    source = str(tmp_path / "audio.wav")
    old = database.create_task(kind="transcription", source_input=source, provider="whisper", model="small")
    database.fail_task(old.id, error_message="old attempt")
    current = database.create_task(kind="transcription", source_input=source, provider="whisper", model="small")
    reporter = ProgressReporter(current.id, callback=database.record_progress)
    reporter.running("transcribing", message="transcribing", stage_progress=0.5,
                     detail={"device": "mps", "processed_seconds": 60, "total_seconds": 120})
    sources = tmp_path / "sources.txt"
    sources.write_text(source + "\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "watch_tasks.py"
    monitor = runpy.run_path(str(script))
    monkeypatch.setattr(sys, "argv", [str(script), "--file", str(sources), "--workspace", str(settings.workspace_root), "--once"])

    assert monitor["main"]() == 0

    output = capsys.readouterr().out
    assert "audio.wav" in output
    assert "转写中" in output
    assert "72.5%" in output
    assert "mps" in output
    assert "0:01:00" in output and "0:02:00" in output
    assert len(database.list_tasks()) == 2
    assert database.get_task(current.id).status == "running"

    # RTK proxy uses pipes: progress must appear before the monitor finishes.
    monkeypatch.setattr(sys, "argv", [str(script), "--file", str(sources), "--workspace", str(settings.workspace_root)])

    def finish_after_first_display(seconds):
        assert "转写中" in capsys.readouterr().out
        database.complete_task(current.id)

    monkeypatch.setattr(monitor["time"], "sleep", finish_after_first_display)
    assert monitor["main"]() == 0
    updates = capsys.readouterr().out
    assert "已完成" in updates
    assert "\x1b[2K" in updates
    assert "\x1b[1A" in updates
