import importlib.util
from pathlib import Path

import pytest

from b2t.transcript_segments import normalize_whisper_segments, text_with_segment_breaks

_spec = importlib.util.spec_from_file_location(
    "repair_transcripts", Path(__file__).resolve().parents[1] / "scripts/repair_transcripts.py"
)
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)


def test_preserves_timing_and_breaks_on_segments_and_sentence_ends():
    segments = normalize_whisper_segments([
        {"start": 0, "end": 2, "text": " 第一句。第二句？", "tokens": [1]},
        {"start": 2, "end": 4, "text": " 无标点时间段"},
    ])
    assert segments[0] == {"start": 0.0, "end": 2.0, "text": "第一句。第二句？"}
    assert text_with_segment_breaks("第一句。第二句？无标点时间段", segments) == "第一句。\n第二句？\n无标点时间段"


def test_incomplete_segments_never_drop_words():
    segments = normalize_whisper_segments([{"start": 0, "end": 2, "text": "第一句"}])
    assert text_with_segment_breaks("第一句还有后文", segments) == "第一句还有后文"


def test_sentence_spanning_segments_is_one_line_with_closing_quote():
    segments = normalize_whisper_segments([
        {"start": 0, "end": 2, "text": "他说：“第一部分，"},
        {"start": 2, "end": 4, "text": "第二部分。”然后结束。"},
    ])
    assert text_with_segment_breaks("他说：“第一部分，第二部分。”然后结束。", segments) == "他说：“第一部分，第二部分。”\n然后结束。"


def test_unpunctuated_text_uses_whisper_time_chunks():
    segments = normalize_whisper_segments([{"start": 0, "end": 3, "text": "第一段"},
                                          {"start": 3, "end": 6, "text": "第二段"}])
    assert text_with_segment_breaks("第一段第二段", segments) == "第一段\n第二段"


def test_unpunctuated_tail_keeps_time_chunks_after_punctuated_intro():
    segments = normalize_whisper_segments([{"start": 0, "end": 3, "text": "开头。"},
                                          {"start": 3, "end": 6, "text": "没有标点"},
                                          {"start": 6, "end": 9, "text": "后半段"}])
    assert text_with_segment_breaks("开头。没有标点后半段", segments) == "开头。\n没有标点\n后半段"


def test_segment_with_internal_newline_never_loses_words():
    import re

    text = "第一行\n还有正文。下一句。"
    segments = normalize_whisper_segments([{"start": 0, "end": 5, "text": text}])
    output = text_with_segment_breaks(text, segments)
    assert re.sub(r"\s+", "", output) == re.sub(r"\s+", "", text)


@pytest.mark.parametrize("value", [None, [{"text": "no timing"}], [{"start": 3, "end": 1, "text": "wrong"}],
                                  [{"start": 0, "end": float("nan"), "text": "wrong"}]])
def test_invalid_timing_is_not_guessed(value):
    assert normalize_whisper_segments(value) == []


def test_never_overwrites_existing_output_and_cleans_tempfile(tmp_path):
    target = tmp_path / "repaired.txt"
    repair.write_new_file(target, "previous")
    with pytest.raises(FileExistsError):
        repair.write_new_file(target, "replacement")
    assert target.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [target]


def test_retranscription_passes_explicit_mps_and_prompt(tmp_path, monkeypatch):
    import json
    import sys
    from b2t.transcribers import whisper_local

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    source = tmp_path / "old.txt"
    source.write_text("旧稿")
    source.with_suffix(".json").write_text(json.dumps({"audio_path": str(audio), "engine": "whisper", "model": "small"}))
    seen = {}

    class FakeWhisper:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def transcribe(self, audio_path, *, prompt):
            assert audio_path == audio
            seen["prompt"] = prompt
            return {"text": "第一句。第二句。", "language": "zh", "device": "mps", "segments": [
                {"start": 0, "end": 1, "text": "第一句。"}, {"start": 1, "end": 2, "text": "第二句。"}]}

    monkeypatch.setattr(whisper_local, "LocalWhisperTranscriber", FakeWhisper)
    monkeypatch.setattr(sys, "argv", ["repair", str(source), "--retranscribe", "--device", "mps"])
    repair.main()
    assert seen == {"model": "small", "language": "zh", "device": "mps", "prompt": "以下是普通话的句子。"}
    assert source.read_text() == "旧稿"
    assert (tmp_path / "old.分段.txt").read_text() == "第一句。\n第二句。\n"


def test_saved_raw_timing_can_repair_simplified_transcript_without_asr(tmp_path, monkeypatch):
    import json
    import sys

    root = tmp_path / "workspace"
    source = root / "transcripts/original/new.txt"
    source.parent.mkdir(parents=True)
    source.write_text("这是第一句。\n这是第二句。\n")
    metadata = root / "metadata/new.json"
    metadata.parent.mkdir()
    metadata.write_text(json.dumps({"engine": "whisper", "text": "這是第一句。這是第二句。",
                                   "output_script": "simplified", "segments": [
                                       {"start": 0, "end": 1, "text": "這是第一句。"},
                                       {"start": 1, "end": 2, "text": "這是第二句。"}]}))
    monkeypatch.setattr(repair, "simplify_output", lambda text: text.replace("這", "这"))
    monkeypatch.setattr(sys, "argv", ["repair", str(source)])
    repair.main()
    assert source.with_name("new.分段.txt").read_text() == source.read_text()
    assert json.loads(source.with_name("new.分段.json").read_text())["segments"][0]["text"] == "這是第一句。"
