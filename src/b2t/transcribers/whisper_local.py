from __future__ import annotations

import importlib
import importlib.util
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from typing import Any

from b2t.i18n import dependency_sync_guidance
from b2t.progress import ProgressReporter
from b2t.transcribers.base import Transcriber


_active_progress: ContextVar[ProgressReporter | None] = ContextVar("whisper_progress", default=None)
_progress_install_lock = Lock()


class LocalWhisperTranscriber(Transcriber):
    name = "whisper"

    def __init__(self, model: str = "small", device: str | None = None, language: str | None = None,
                 initial_prompt: str | None = None) -> None:
        self.model_name = model
        selected_device = (device or "auto").strip().lower()
        if selected_device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("Whisper device must be auto, cpu, cuda or mps")
        self._auto_device = selected_device == "auto"
        self.device = None if selected_device == "auto" else selected_device
        self.language = language.strip().lower() if language and language.strip().lower() != "auto" else None
        self.initial_prompt = initial_prompt
        self._model: Any | None = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        if progress is not None:
            progress.running("loading_model", message="loading_model", indeterminate=True,
                             detail={"model": self.model_name})
        model = self._ensure_model()
        if progress is not None:
            progress.running("transcribing", message="transcribing", stage_progress=0.0,
                             detail={"model": self.model_name, "device": self.device})
        transcribe_options: dict[str, Any] = {
            "initial_prompt": prompt or self.initial_prompt or None,
            # `verbose=False` keeps Whisper text output quiet while still driving the internal tqdm loop.
            "verbose": False,
        }
        if self.language is not None:
            transcribe_options["language"] = self.language
        if self.device in {"cpu", "mps"}:
            transcribe_options["fp16"] = False
        with whisper_progress(progress):
            result = model.transcribe(str(audio_path), **transcribe_options)
        text = (result.get("text") or "").strip()
        return {
            "text": text,
            "segments": result.get("segments", []),
            "language": result.get("language"),
            "device": self.device,
            "model": self.model_name,
            "initial_prompt": transcribe_options["initial_prompt"],
        }

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            import whisper
        except ImportError as exc:
            raise RuntimeError(build_whisper_import_error_message()) from exc

        if self.device is None:
            if whisper.torch.cuda.is_available():
                self.device = "cuda"
            elif whisper.torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        try:
            self._model = whisper.load_model(self.model_name, device=self.device)
        except NotImplementedError:
            if not self._auto_device or self.device != "mps":
                raise
            warnings.warn("This PyTorch build cannot load Whisper on MPS; using CPU instead.", RuntimeWarning)
            self.device = "cpu"
            self._model = whisper.load_model(self.model_name, device=self.device)
        return self._model


def build_whisper_import_error_message(*, whisper_available: bool | None = None) -> str:
    if whisper_available is None:
        whisper_available = importlib.util.find_spec("whisper") is not None

    if whisper_available:
        return (
            "Whisper is installed, but the Python environment looks broken. "
            "Recreate `.venv` and sync the required extras again. "
            f"{dependency_sync_guidance('en-US')} "
            "Whisper currently needs Python 3.12 or below."
        )

    return (
        "Whisper support is not installed. "
        f"{dependency_sync_guidance('en-US')} "
        "Whisper currently needs Python 3.12 or below."
    )


@contextmanager
def whisper_progress(progress: ProgressReporter | None):
    if progress is not None:
        module = importlib.import_module("whisper.transcribe")
        with _progress_install_lock:
            if not isinstance(module.tqdm, _WhisperTqdm):
                module.tqdm = _WhisperTqdm(module.tqdm)
    token = _active_progress.set(progress)
    try:
        yield
    finally:
        _active_progress.reset(token)


class _WhisperTqdm:
    """Route only Whisper's bars to the reporter in the calling context."""

    def __init__(self, module: Any) -> None:
        self.module = module

    def tqdm(self, *args, **kwargs):
        progress = _active_progress.get()
        if progress is None:
            return self.module.tqdm(*args, **kwargs)
        return WhisperProgressTqdm(progress, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.module, name)


class WhisperProgressTqdm:
    def __init__(self, progress: ProgressReporter, *args, total: int | None = None, disable: bool = False, **kwargs) -> None:
        self.progress = progress
        self.total = total or 0
        self.disable = disable
        self.n = 0
        self.detail = dict(progress.snapshot.detail)

    def __enter__(self) -> "WhisperProgressTqdm":
        if not self.disable:
            self.progress.running("transcribing", message="transcribing", stage_progress=0.0, detail=self.detail)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def update(self, amount: int = 1) -> None:
        self.n += amount
        if self.disable:
            return
        if self.total <= 0:
            self.progress.running("transcribing", message="transcribing", indeterminate=True, detail=self.detail)
            return
        self.progress.running(
            "transcribing",
            message="transcribing",
            stage_progress=min(1.0, self.n / self.total),
            detail={**self.detail, "processed_seconds": self.n / 100, "total_seconds": self.total / 100},
        )

    def refresh(self) -> None:
        return

    def close(self) -> None:
        return
