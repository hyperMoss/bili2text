from __future__ import annotations

from pathlib import Path

from b2t.config import Settings
from b2t.downloaders import YtDlpDownloader
from b2t.pipeline import B2TPipeline
from b2t.transcribers import LocalWhisperTranscriber
from b2t.user_config import AppConfig, DEFAULT_CHINESE_PROMPT


def build_pipeline(
    *,
    settings: Settings,
    config: AppConfig,
    provider: str | None = None,
    model: str | None = None,
    audio_language: str | None = None,
    device: str | None = None,
) -> B2TPipeline:
    selected_provider = (provider or config.default_provider).strip().lower()
    selected_model = (model or config.default_model).strip()
    if audio_language and audio_language != "auto" and selected_provider != "whisper":
        raise ValueError("--audio-language is currently supported by Whisper only")
    if device is not None and selected_provider != "whisper":
        raise ValueError("--device is currently supported by Whisper only")
    selected_device = (device or config.whisper.device).strip().lower()
    if selected_provider == "whisper" and selected_device not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError("Whisper device must be auto, cpu, cuda or mps")
    selected_language = audio_language if audio_language is not None else config.whisper.audio_language
    initial_prompt = config.whisper.initial_prompt
    if selected_language.strip().lower() not in {"zh", "chinese"} and initial_prompt == DEFAULT_CHINESE_PROMPT:
        initial_prompt = ""

    def create_transcriber():
        if selected_provider == "whisper":
            transcriber = LocalWhisperTranscriber(model=selected_model or "small", language=selected_language,
                                                device=selected_device, initial_prompt=initial_prompt)
        elif selected_provider == "sensevoice":
            from b2t.transcribers.sensevoice_local import SenseVoiceSmallTranscriber

            model_dir_text = selected_model or config.sensevoice.model_dir
            if not model_dir_text:
                raise RuntimeError("SenseVoice provider requires a local model directory. Run `bili2text bootstrap` first.")
            transcriber = SenseVoiceSmallTranscriber(
                model_dir=Path(model_dir_text).expanduser(),
                language=config.sensevoice.language,
                use_itn=config.sensevoice.use_itn,
            )
        elif selected_provider == "volcengine":
            from b2t.transcribers.volcengine import VolcengineFlashTranscriber

            transcriber = VolcengineFlashTranscriber(
                api_key=config.volcengine.api_key,
                app_key=config.volcengine.app_key,
                access_key=config.volcengine.access_key,
                resource_id=config.volcengine.resource_id,
                model_name=selected_model or config.volcengine.model_name,
                use_itn=config.volcengine.use_itn,
            )
        else:
            raise RuntimeError(f"Unsupported provider: {selected_provider}")

        return transcriber

    return B2TPipeline(
        settings=settings,
        downloader=YtDlpDownloader(),
        transcriber_factory=create_transcriber,
        simplify_whisper=config.whisper.simplified,
    )
