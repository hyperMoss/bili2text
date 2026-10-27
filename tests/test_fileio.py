from pathlib import Path

import pytest

from b2t.fileio import atomic_write_text


def test_atomic_write_never_overwrites_an_existing_transcript(tmp_path: Path) -> None:
    path = tmp_path / "original.txt"
    atomic_write_text(path, "old\n")
    with pytest.raises(FileExistsError):
        atomic_write_text(path, "new\n")
    assert path.read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_unicode_and_replace_when_explicit(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    atomic_write_text(path, '{"title": "旧"}')
    atomic_write_text(path, '{"title": "新"}', replace=True)
    assert path.read_text(encoding="utf-8") == '{"title": "新"}'
