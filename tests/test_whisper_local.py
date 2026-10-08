from b2t.progress import ProgressReporter
from b2t.transcribers.whisper_local import (
    LocalWhisperTranscriber,
    WhisperProgressTqdm,
    build_whisper_import_error_message,
)


def test_build_whisper_import_error_message_reports_missing_install() -> None:
    message = build_whisper_import_error_message(
        whisper_available=False,
    )

    assert "Whisper support is not installed." in message
    assert "uv sync --extra whisper --extra web" in message


def test_build_whisper_import_error_message_reports_broken_environment() -> None:
    message = build_whisper_import_error_message(
        whisper_available=True,
    )

    assert "Whisper is installed, but the Python environment looks broken." in message
    assert ".venv" in message


def test_whisper_progress_tqdm_reports_fractional_progress() -> None:
    events = []
    reporter = ProgressReporter("task-1", callback=events.append)
    bar = WhisperProgressTqdm(reporter, total=100, disable=False)

    with bar:
        bar.update(25)
        bar.update(25)

    assert events[-1].stage == "transcribing"
    assert round(events[-1].percent, 3) == 0.725


def test_explicit_audio_language_is_passed_to_whisper(tmp_path, monkeypatch):
    class Model:
        def transcribe(self, path, **options):
            assert options["language"] == "zh"
            assert options["fp16"] is False
            return {"text": "中文文字稿", "language": "zh"}

    transcriber = LocalWhisperTranscriber(device="cpu", language="zh")
    monkeypatch.setattr(transcriber, "_ensure_model", lambda: Model())
    assert transcriber.transcribe(tmp_path / "audio.wav")["language"] == "zh"


def test_whisper_progress_includes_processed_audio_duration():
    events = []
    reporter = ProgressReporter("audio-progress", callback=events.append)
    bar = WhisperProgressTqdm(reporter, total=497787)
    bar.update(6000)
    assert events[-1].detail["processed_seconds"] == 60
    assert events[-1].detail["total_seconds"] == 4977.87


def test_explicit_mps_device_is_passed_to_model_loader(monkeypatch):
    import sys
    from types import SimpleNamespace

    loaded = []
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False),
                            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)))
    whisper = SimpleNamespace(torch=torch, load_model=lambda model, device: loaded.append(device) or object())
    monkeypatch.setitem(sys.modules, "whisper", whisper)
    transcriber = LocalWhisperTranscriber(device="mps")
    transcriber._ensure_model()
    assert loaded == ["mps"]


def test_configured_prompt_is_used_and_explicit_prompt_overrides_it(tmp_path, monkeypatch):
    seen = []

    class Model:
        def transcribe(self, path, **kwargs):
            seen.append(kwargs["initial_prompt"])
            return {"text": "中文", "language": "zh"}

    transcriber = LocalWhisperTranscriber(device="cpu", language="zh", initial_prompt="以下是普通话的句子。")
    monkeypatch.setattr(transcriber, "_ensure_model", lambda: Model())
    first = transcriber.transcribe(tmp_path / "audio.wav", prompt="")
    second = transcriber.transcribe(tmp_path / "audio.wav", prompt="自定义提示")
    assert seen == ["以下是普通话的句子。", "自定义提示"]
    assert first["initial_prompt"] == seen[0]
    assert second["initial_prompt"] == seen[1]
