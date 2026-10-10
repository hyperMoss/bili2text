import pytest

from b2t.config import Settings
from b2t.factory import build_pipeline
from b2t.user_config import AppConfig, DEFAULT_CHINESE_PROMPT


def test_whisper_defaults_and_device_override_are_lazy(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr("b2t.factory.LocalWhisperTranscriber", lambda **kw: seen.append(kw) or object())
    config = AppConfig()
    config.whisper.device = "cpu"
    pipeline = build_pipeline(settings=Settings.from_workspace(tmp_path), config=config, device="mps")
    assert seen == []
    pipeline.transcriber
    assert seen == [{"model": "small", "language": "zh", "device": "mps", "initial_prompt": DEFAULT_CHINESE_PROMPT}]
    assert pipeline.simplify_whisper is True


def test_explicit_non_chinese_language_does_not_get_chinese_prompt(tmp_path):
    pipeline = build_pipeline(settings=Settings.from_workspace(tmp_path), config=AppConfig(), audio_language="en")
    assert pipeline.transcriber.language == "en"
    assert pipeline.transcriber.initial_prompt == ""


def test_config_can_disable_conversion_and_enable_auto_language(tmp_path):
    config = AppConfig()
    config.whisper.audio_language = "auto"
    config.whisper.simplified = False
    pipeline = build_pipeline(settings=Settings.from_workspace(tmp_path), config=config)
    assert pipeline.transcriber.language is None
    assert not pipeline.simplify_whisper


@pytest.mark.parametrize("kwargs", [{"device": "bogus"}, {"provider": "sensevoice", "device": "mps"}])
def test_invalid_devices_fail_before_loading_models(tmp_path, kwargs):
    with pytest.raises(ValueError):
        build_pipeline(settings=Settings.from_workspace(tmp_path), config=AppConfig(), **kwargs)
