"""Crash-safe file writes: never replace an existing transcription by accident."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, replace: bool = False) -> None:
    """Persist UTF-8 text atomically; creating a new file never clobbers an existing one.

    A temporary file is fsynced before it is published. `os.link` installs a
    new filename only if it is free; `os.replace` is reserved for validated
    metadata updates.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
