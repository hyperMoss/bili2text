from __future__ import annotations

import html
import re


class SubtitleLookupError(RuntimeError):
    """Subtitle availability is unknown; downloading media would be premature."""


def srt_to_text(content: str) -> str:
    """Extract spoken lines from SRT, keeping repeated speech and line breaks."""
    lines = []
    for block in re.split(r"\n\s*\n", content.lstrip("\ufeff").replace("\r\n", "\n").strip()):
        parts = block.splitlines()
        timing = next((i for i, line in enumerate(parts) if re.match(
            r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{1,2}:\d{2}:\d{2}[,.]\d{3}", line,
        )), None)
        if timing is None:
            continue
        for line in parts[timing + 1:]:
            text = html.unescape(re.sub(r"<[^>]+>", "", line)).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def subtitle_language_priority(language: str) -> tuple[int, str]:
    language = language.lower()
    if language in {"zh-cn", "zh-hans", "zh"}:
        rank = 0
    elif language.startswith("ai-zh"):
        rank = 1
    elif language.startswith("zh"):
        rank = 2
    else:
        rank = 3
    return rank, language
