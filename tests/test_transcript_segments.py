import pytest

from b2t.transcript_segments import normalize_whisper_segments, text_with_segment_breaks


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
