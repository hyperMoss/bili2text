"""Keep Whisper timing and render chunks without changing recognized words."""
from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _simplifier():
    from opencc import OpenCC
    return OpenCC("t2s")


def simplify_chinese(text: str) -> str:
    return _simplifier().convert(text)


def normalize_whisper_segments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output = []
    previous = -1.0
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return []
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            return []
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end and start >= previous):
            return []
        previous = start
        if item["text"].strip():
            output.append({"start": start, "end": end, "text": item["text"].strip()})
    return output


def text_with_segment_breaks(text: str, segments: list[dict[str, Any]]) -> str:
    """Use sentence endings, with timing chunks as the unpunctuated fallback.

    Merge a sentence that spans chunks. If no sentence ending appears for 30
    seconds, use a timing boundary. Incomplete chunks leave original text intact.
    """
    joined = "".join(item["text"] for item in segments)
    if not segments or re.sub(r"\s+", "", joined) != re.sub(r"\s+", "", text):
        return text.strip()
    if not re.search(r"[。！？!?]", text):
        return "\n".join(item["text"] for item in segments)
    lines = []
    pending = []
    pending_start = None
    sentence_end = r"[。！？!?][\"”’」』）)]*"
    for item in segments:
        if pending and pending_start is not None and item["start"] - pending_start >= 30:
            lines.extend(pending)
            pending = []
        if not pending:
            pending_start = item["start"]
        parts = re.findall(rf".*?{sentence_end}|.+$", item["text"], flags=re.DOTALL)
        for part in parts:
            pending.append(part.strip())
            if re.search(rf"{sentence_end}$", part):
                sentence = ""
                for chunk in pending:
                    separator = " " if sentence and sentence[-1].isascii() and sentence[-1].isalnum() and chunk[0].isascii() and chunk[0].isalnum() else ""
                    sentence += separator + chunk
                lines.append(sentence)
                pending = []
                pending_start = item["start"]
    if pending:
        lines.extend(pending)
    return "\n".join(lines)
