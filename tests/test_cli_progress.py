import pytest

from b2t.cli_progress import TqdmTaskRenderer
from b2t.models import ProgressSnapshot


def test_renderer_keeps_fractional_progress_and_omits_misleading_eta():
    renderer = TqdmTaskRenderer("zh-CN")
    try:
        renderer(ProgressSnapshot(task_id="task", status="running", stage="transcribing", message="transcribing", percent=0.55421867, detail={"processed_seconds": 60, "total_seconds": 4977.87}))
        assert renderer._bar.n == pytest.approx(55.421867)
        display = str(renderer._bar)
        assert "55.4%" in display
        assert "01:00 / 1:22:58" in display
        assert "<" not in display  # earlier stages do not predict transcription speed
    finally:
        renderer._bar.close()


def test_stage_updates_reuse_the_same_line_and_fit_terminal_width(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "80")
    renderer = TqdmTaskRenderer("zh-CN", label="[1/3] " + "很长的本地视频文件名" * 8)
    try:
        for stage, percent in [("preparing", 0), ("downloading", 0.2), ("transcribing", 0.6)]:
            renderer(ProgressSnapshot(task_id="task", status="running", stage=stage, message=stage, percent=percent))
        output = capsys.readouterr()
        assert "\n" not in output.out + output.err
        assert "\r" in output.err
        from rich.cells import cell_len
        assert all(cell_len(line) < 80 for line in output.err.split("\r"))
    finally:
        renderer.close()
