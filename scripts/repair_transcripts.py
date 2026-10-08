#!/usr/bin/env python3
"""Use Whisper timing to repair TXT line breaks, keeping originals.

Run from project root:
  uv run --no-sync python scripts/repair_transcripts.py FILE [FILE ...] --retranscribe

Without --retranscribe, only saved segments are used. Missing timing is never
guessed. Reuse local audio without video downloads. Save STEM.分段.txt and JSON.
Optional --simplified requires scripts/requirements-transcript-repair.txt.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from b2t.transcript_segments import normalize_whisper_segments, text_with_segment_breaks


def write_new_file(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".repair-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def metadata_for(source: Path) -> dict:
    candidates = [source.with_suffix(".json")]
    if source.parent.name in {"original", "edited"} and source.parent.parent.name == "transcripts":
        candidates.append(source.parent.parent.parent / "metadata" / (source.stem + ".json"))
    candidates.append(ROOT / ".b2t/metadata" / (source.stem + ".json"))
    for path in candidates:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def simplify_output(text: str) -> str:
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise RuntimeError("简体转换需要安装依赖: uv pip install --python .venv/bin/python -r scripts/requirements-transcript-repair.txt") from exc
    return OpenCC("t2s").convert(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--retranscribe", action="store_true", help="无时间轴时用已有音频重新识别")
    parser.add_argument("--model", help="覆盖原任务模型（默认原模型或 small）")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--device", choices=["cpu", "cuda", "mps"], help="指定 Whisper 计算设备（默认自动选择）")
    parser.add_argument("--simplified", action="store_true", help="本地繁简转换，清除提示词未消除的繁体残留")
    parser.add_argument("--prompt", default="以下是普通话的句子。")
    args = parser.parse_args()
    jobs = []
    for source in args.files:
        source = source.expanduser().resolve(strict=True)
        if source.suffix.lower() != ".txt":
            parser.error(f"仅支持 TXT: {source}")
        target = source.with_name(source.stem + ".分段.txt")
        sidecar = target.with_suffix(".json")
        if target.exists() or sidecar.exists():
            parser.error(f"输出已存在，请先移动旧输出: {target}")
        metadata = metadata_for(source)
        segments = normalize_whisper_segments(metadata.get("segments"))
        audio = None
        if not segments:
            if not args.retranscribe:
                parser.error(f"未保存时间轴: {source.name}；添加 --retranscribe 使用已有音频重新识别")
            if not metadata.get("audio_path"):
                parser.error(f"没有本地音频记录: {source.name}")
            audio = Path(metadata["audio_path"]).expanduser()
            if not audio.is_absolute():
                audio = ROOT / audio
            if not audio.is_file():
                parser.error(f"本地音频不存在: {audio}")
        jobs.append((source, target, sidecar, metadata, segments, audio))
    transcribers = {}
    for index, (source, target, sidecar, metadata, segments, audio) in enumerate(jobs, 1):
        original_bytes = source.read_bytes()
        text = original_bytes.decode("utf-8-sig").strip()
        if segments and isinstance(metadata.get("text"), str):
            text = metadata["text"].strip()
        print(f"[{index}/{len(jobs)}] 处理: {source.name}", flush=True)
        if audio is not None:
            from b2t.transcribers.whisper_local import LocalWhisperTranscriber

            model = args.model or (metadata.get("model") if metadata.get("engine") == "whisper" else None) or "small"
            key = (model, args.language, args.device)
            if key not in transcribers:
                transcribers[key] = LocalWhisperTranscriber(model=model, language=args.language, device=args.device)
            print(f"重新识别本地音频: {audio.name}；模型 {model}；设备 {args.device or 'auto'}；提示 {args.prompt}", flush=True)
            result = transcribers[key].transcribe(audio, prompt=args.prompt)
            segments = normalize_whisper_segments(result.get("segments"))
            text = result.get("text", "").strip()
            metadata = {"engine": "whisper", "model": model, "language": result.get("language"),
                        "audio_path": str(audio), "initial_prompt": args.prompt, "device": result.get("device")}
        if not segments or not text:
            raise RuntimeError("Whisper 没有返回有效时间段，停止保存")
        formatted = text_with_segment_breaks(text, segments)
        if len(segments) > 1 and "\n" not in formatted:
            raise RuntimeError("时间段未覆盖完整正文，停止保存以避免丢字")
        if source.read_bytes() != original_bytes:
            raise RuntimeError(f"处理期间原文件被修改，停止保存: {source}")
        if args.simplified or metadata.get("output_script") == "simplified":
            formatted = simplify_output(formatted)
            metadata["output_script"] = "simplified"
        metadata = {**metadata, "source_transcript": str(source), "text": text,
                    "segments": segments, "format": "whisper-sentence-lines-with-timing-fallback"}
        write_new_file(sidecar, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        write_new_file(target, formatted + "\n")
        print(f"已保存: {target}（{len(formatted.splitlines())} 行）", flush=True)


if __name__ == "__main__":
    main()
