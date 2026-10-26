from b2t.progress import ProgressReporter
from b2t.transcribers.whisper_local import (
    LocalWhisperTranscriber,
    WhisperProgressTqdm,
    build_whisper_import_error_message,
    whisper_progress,
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


def test_concurrent_whisper_progress_stays_with_its_task(monkeypatch):
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    original = lambda *args, **kwargs: None
    tqdm_module = SimpleNamespace(tqdm=original)
    module = SimpleNamespace(tqdm=tqdm_module)
    monkeypatch.setitem(sys.modules, "whisper.transcribe", module)
    first_entered, second_entered, first_updated, second_finished = (Event() for _ in range(4))
    first_events, second_events = [], []

    def first():
        with whisper_progress(ProgressReporter("first", callback=first_events.append)):
            first_entered.set()
            assert second_entered.wait(5)
            module.tqdm.tqdm(total=10000).update(3000)
            first_updated.set()
            assert second_finished.wait(5)

    def second():
        assert first_entered.wait(5)
        try:
            with whisper_progress(ProgressReporter("second", callback=second_events.append)):
                second_entered.set()
                assert first_updated.wait(5)
                module.tqdm.tqdm(total=20000).update(6000)
        finally:
            second_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(first), executor.submit(second)]
        for future in futures:
            future.result(timeout=10)

    assert [event.detail["total_seconds"] for event in first_events] == [100]
    assert [event.detail["total_seconds"] for event in second_events] == [200]
    assert tqdm_module.tqdm is original


def test_auto_device_selects_available_mps(monkeypatch):
    import sys
    from types import SimpleNamespace

    loaded = []
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False),
                            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)))
    whisper = SimpleNamespace(torch=torch, load_model=lambda model, device: loaded.append(device) or object())
    monkeypatch.setitem(sys.modules, "whisper", whisper)
    transcriber = LocalWhisperTranscriber()
    transcriber._ensure_model()
    assert loaded == ["mps"]


def test_mps_uses_float32_for_transcription(tmp_path, monkeypatch):
    class Model:
        def transcribe(self, path, **options):
            assert options["fp16"] is False
            return {"text": "中文文字稿"}

    transcriber = LocalWhisperTranscriber(device="mps")
    monkeypatch.setattr(transcriber, "_ensure_model", lambda: Model())
    transcriber.transcribe(tmp_path / "audio.wav")


def test_progress_context_restores_after_error_and_does_not_capture_unreported_calls(monkeypatch):
    import sys
    from types import SimpleNamespace
    import pytest

    normal_bar = object()
    module = SimpleNamespace(tqdm=SimpleNamespace(tqdm=lambda **kwargs: normal_bar))
    monkeypatch.setitem(sys.modules, "whisper.transcribe", module)
    events = []
    reporter = ProgressReporter("outer", callback=events.append)
    with whisper_progress(reporter):
        with pytest.raises(RuntimeError):
            with whisper_progress(ProgressReporter("failed")):
                raise RuntimeError("transcription failed")
        with whisper_progress(None):
            assert module.tqdm.tqdm(total=100) is normal_bar
        module.tqdm.tqdm(total=100).update(25)
    assert module.tqdm.tqdm(total=100) is normal_bar
    assert len(events) == 1
    assert events[0].task_id == "outer"


def test_auto_device_prefers_cuda_and_falls_back_to_cpu_without_accelerators(monkeypatch):
    import sys
    from types import SimpleNamespace

    for cuda, mps, expected in [(True, True, "cuda"), (False, False, "cpu")]:
        loaded = []
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda),
                                backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)))
        whisper = SimpleNamespace(torch=torch, load_model=lambda model, device: loaded.append(device) or object())
        monkeypatch.setitem(sys.modules, "whisper", whisper)
        LocalWhisperTranscriber()._ensure_model()
        assert loaded == [expected]


def test_unsupported_mps_falls_back_only_for_auto_selection(monkeypatch):
    import sys
    from types import SimpleNamespace
    import pytest

    loaded = []

    def load_model(model, device):
        loaded.append(device)
        if device == "mps":
            raise NotImplementedError("sparse MPS tensors unsupported")
        return object()

    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False),
                            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)))
    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(torch=torch, load_model=load_model))
    transcriber = LocalWhisperTranscriber()
    with pytest.warns(RuntimeWarning, match="using CPU"):
        transcriber._ensure_model()
    assert transcriber.device == "cpu"
    assert loaded == ["mps", "cpu"]
    with pytest.raises(NotImplementedError):
        LocalWhisperTranscriber(device="mps")._ensure_model()


def test_model_loading_is_reported_before_load_and_device_survives_progress(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    module = SimpleNamespace(tqdm=SimpleNamespace(tqdm=None))
    monkeypatch.setitem(sys.modules, "whisper.transcribe", module)
    events = []
    reporter = ProgressReporter("model-progress", callback=events.append)

    class Model:
        def transcribe(self, path, **options):
            with module.tqdm.tqdm(total=10000) as bar:
                bar.update(5000)
            return {"text": "中文文字稿"}

    def load():
        assert events[-1].stage == "loading_model"
        assert events[-1].indeterminate
        return Model()

    transcriber = LocalWhisperTranscriber(device="mps")
    monkeypatch.setattr(transcriber, "_ensure_model", load)
    transcriber.transcribe(tmp_path / "audio.wav", progress=reporter)
    assert events[-1].detail == {"model": "small", "device": "mps", "processed_seconds": 50, "total_seconds": 100}
