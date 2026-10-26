from b2t.config import Settings
from b2t.downloaders.ytdlp import YtDlpDownloader
from b2t.models import SourceRef


def test_ytdlp_options_keep_single_video_output_template_by_default(tmp_path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["noplaylist"] is True
    assert "playlist_items" not in opts
    assert opts["outtmpl"] == str(settings.downloads_dir / "%(id)s.%(ext)s")


def test_ytdlp_options_select_playlist_item_when_page_is_set(tmp_path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    source = SourceRef(
        raw_input="https://www.bilibili.com/video/BV1xx411c7XD?p=2",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD?p=2",
        page=2,
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["noplaylist"] is False
    assert opts["playlist_items"] == "2"
    assert opts["outtmpl"] == str(settings.downloads_dir / "BV1xx411c7XD.p02.%(ext)s")


def test_ytdlp_options_bypass_proxy_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("B2T_USE_PROXY", raising=False)
    settings = Settings.from_workspace(tmp_path / ".b2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["proxy"] == ""


def test_ytdlp_options_keep_system_proxy_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("B2T_USE_PROXY", "1")
    settings = Settings.from_workspace(tmp_path / ".b2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert "proxy" not in opts


def test_ytdlp_options_falsey_proxy_flag_still_bypasses(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("B2T_USE_PROXY", "0")
    settings = Settings.from_workspace(tmp_path / ".b2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["proxy"] == ""


def test_ytdlp_options_use_workspace_cookie_file_when_present(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("B2T_COOKIE_FILE", raising=False)
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    cookie_file = settings.workspace_root / "cookies.txt"
    cookie_file.write_text("# placeholder cookie file\n", encoding="utf-8")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["cookiefile"] == str(cookie_file)


def test_ytdlp_options_env_cookie_file_takes_priority(tmp_path, monkeypatch) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    workspace_cookie_file = settings.workspace_root / "cookies.txt"
    workspace_cookie_file.write_text("# workspace cookie file\n", encoding="utf-8")
    env_cookie_file = tmp_path / "custom-cookies.txt"
    env_cookie_file.write_text("# env cookie file\n", encoding="utf-8")
    monkeypatch.setenv("B2T_COOKIE_FILE", str(env_cookie_file))
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["cookiefile"] == str(env_cookie_file)


def test_download_reports_progress_without_a_second_native_progress_bar(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from b2t.progress import ProgressReporter

    settings = Settings.from_workspace(tmp_path / ".b2t")
    source = SourceRef(raw_input="BV1xx411c7XD", kind="bilibili", display_name="video", bv="BV1xx411c7XD")
    output_path = settings.downloads_dir / "BV1xx411c7XD.mp4"
    events = []

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert self.options["noprogress"] is True
            for hook in self.options["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                hook({"status": "finished"})
            output_path.write_bytes(b"video")
            return {"id": source.bv, "title": "video", "requested_downloads": [{"filepath": str(output_path)}]}

        def sanitize_info(self, info):
            return info

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Downloader))
    result = YtDlpDownloader().download(source, settings, progress=ProgressReporter("download", callback=events.append))
    assert result.video_path == output_path
    assert events[0].percent == 0.2
    assert events[-1].message == "download_finished"
