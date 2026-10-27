from __future__ import annotations

import os
import threading
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import uvicorn
from fastapi import FastAPI


@contextmanager
def running_server(app: FastAPI, *, host: str = "127.0.0.1", port: int = 0) -> Iterator[tuple[str, threading.Thread]]:
    """Bind once, wait for startup, and stop the server when its UI closes."""
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    sock = config.bind_socket()
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("bili2text UI server failed to start")
            time.sleep(0.01)
        browser_host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
        if ":" in browser_host:
            browser_host = f"[{browser_host}]"
        yield f"http://{browser_host}:{sock.getsockname()[1]}", thread
    finally:
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=5)
        sock.close()


def run_browser(*, app: FastAPI, host: str, port: int) -> None:
    with running_server(app, host=host, port=port) as (url, thread):
        webbrowser.open_new_tab(url)
        try:
            while thread.is_alive():
                thread.join(timeout=0.5)
        except KeyboardInterrupt:
            pass


def run_window(*, app: FastAPI) -> None:
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    with running_server(app) as (url, _thread):
        webview.create_window(
            "bili2text", url, width=1120, height=820, min_size=(760, 600),
            text_select=True, confirm_close=True,
        )
        webview.start()


def open_path(path: Path) -> None:
    path = path.expanduser().resolve()
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        webbrowser.open(path.as_uri())
