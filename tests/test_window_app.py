from pathlib import Path
from types import SimpleNamespace

import pytest

from b2t import window_app


@pytest.mark.skipif(window_app.os.name == "nt", reason="File URI opener is used on POSIX")
def test_open_workspace_with_relative_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    opened = []
    monkeypatch.setattr(window_app.webbrowser, "open", opened.append)
    app = window_app.WindowApp.__new__(window_app.WindowApp)
    app.workspace_var = SimpleNamespace(get=lambda: ".b2t")

    app._open_workspace()

    workspace = tmp_path / ".b2t"
    assert workspace.is_dir()
    assert opened == [workspace.as_uri()]


@pytest.mark.skipif(window_app.os.name == "nt", reason="File URI opener is used on POSIX")
@pytest.mark.parametrize("path_kind", ["relative", "absolute", "home"])
def test_open_transcript_resolves_path(tmp_path, monkeypatch, path_kind) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript = tmp_path / "文字稿 with spaces.txt"
    transcript.write_text("transcript", encoding="utf-8")
    paths = {
        "relative": Path(transcript.name),
        "absolute": transcript,
        "home": Path("~") / transcript.name,
    }
    opened = []
    monkeypatch.setattr(window_app.webbrowser, "open", opened.append)
    app = window_app.WindowApp.__new__(window_app.WindowApp)
    app.latest_result = SimpleNamespace(transcript_path=paths[path_kind])

    app._open_transcript()

    assert opened == [transcript.as_uri()]
