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
