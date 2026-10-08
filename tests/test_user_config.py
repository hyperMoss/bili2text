from pathlib import Path

from b2t.config import Settings
from b2t.user_config import AppConfig


def test_app_config_round_trip(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    config = AppConfig(
        default_provider="sensevoice",
        default_model="C:/models/sensevoice-small",
        language="en-US",
    )
    config.enabled_features = ["web", "window"]
    config.sensevoice.model_dir = "C:/models/sensevoice-small"
    config.volcengine.api_key = "secret"
    config.save(settings)

    loaded = AppConfig.load(settings)
    assert loaded.language == "en-US"
    assert loaded.enabled_features == ["web", "window"]
    assert loaded.default_provider == "sensevoice"
    assert loaded.default_model == "C:/models/sensevoice-small"
    assert loaded.sensevoice.model_dir == "C:/models/sensevoice-small"
    assert loaded.volcengine.api_key == "secret"


def test_old_config_gets_chinese_whisper_defaults(tmp_path):
    import json

    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    settings.config_path.write_text(json.dumps({"default_provider": "whisper", "default_model": "small"}))
    config = AppConfig.load(settings)
    assert config.whisper.audio_language == "zh"
    assert config.whisper.device == "auto"
    assert config.whisper.initial_prompt == "以下是普通话的句子。"
    assert config.whisper.simplified is True
    config.whisper.device = "mps"
    config.whisper.simplified = False
    config.save(settings)
    assert AppConfig.load(settings).whisper.device == "mps"
    assert AppConfig.load(settings).whisper.simplified is False
