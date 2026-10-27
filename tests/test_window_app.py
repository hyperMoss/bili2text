from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from b2t import window_app


@pytest.mark.skipif(window_app.os.name == "nt", reason="File URI opener is used on POSIX")
@pytest.mark.parametrize("path_kind", ["relative", "absolute", "home"])
def test_open_path_resolves_files_and_directories(tmp_path, monkeypatch, path_kind) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    opened = []
    monkeypatch.setattr(window_app.webbrowser, "open", opened.append)
    for target in [tmp_path / "文字稿 with spaces.txt", tmp_path / "workspace"]:
        if target.suffix:
            target.touch()
        else:
            target.mkdir()
        paths = {"relative": Path(target.name), "absolute": target, "home": Path("~") / target.name}
        window_app.open_path(paths[path_kind])
        assert opened[-1] == target.as_uri()


def test_window_serves_app_until_native_window_closes(monkeypatch) -> None:
    app = FastAPI()

    @app.get("/")
    def home():
        return {"ui": "shared"}

    urls = []
    native = SimpleNamespace(settings={})

    def create_window(title, url, **kwargs):
        urls.append(url)
        assert httpx.get(url, trust_env=False).json() == {"ui": "shared"}

    def start():
        assert httpx.get(urls[0], trust_env=False).status_code == 200

    native.create_window = create_window
    native.start = start
    monkeypatch.setitem(sys.modules, "webview", native)
    window_app.run_window(app=app)
    assert native.settings["ALLOW_DOWNLOADS"] is True
    with pytest.raises(httpx.ConnectError):
        httpx.get(urls[0], trust_env=False)


def test_server_startup_failure_is_reported_and_socket_released() -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def failing_lifespan(app):
        raise RuntimeError("startup failed")
        yield

    with pytest.raises(RuntimeError, match="failed to start"):
        with window_app.running_server(FastAPI(lifespan=failing_lifespan)):
            pytest.fail("must not open a UI when startup failed")
