from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from b2t.config import Settings
from b2t.downloaders.base import Downloader
from b2t.models import DownloadResult, SourceRef, SubtitleResult
from b2t.subtitles import SubtitleLookupError, srt_to_text, subtitle_language_priority


class YtDlpDownloader(Downloader):
    name = "yt-dlp"

    def fetch_subtitle(self, source: SourceRef, settings: Settings, *, progress=None) -> SubtitleResult | None:
        """Fetch captions via metadata only; never request video or audio bytes."""
        if source.kind != "bilibili":
            return None
        if progress is not None:
            progress.running("checking_subtitles", message="checking_subtitles", indeterminate=True)
        logger = _SubtitleLogger()
        try:
            from yt_dlp import YoutubeDL

            options = self._build_ydl_opts(source, settings)
            options.update({
                "skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
                "subtitleslangs": ["all", "-danmaku"], "subtitlesformat": "srt",
                "ignore_no_formats_error": True, "logger": logger, "no_warnings": False,
            })
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(source.url or f"https://www.bilibili.com/video/{source.bv}", download=False)
                if info and info.get("entries") is not None:
                    info = next((entry for entry in info["entries"] if entry), None)
                if not info:
                    raise SubtitleLookupError("Bilibili returned no subtitle metadata; video download was skipped.")
                tracks = {**(info.get("automatic_captions") or {}), **(info.get("subtitles") or {})}
                for language in sorted(tracks, key=subtitle_language_priority):
                    if language.lower() == "danmaku":
                        continue
                    for track in tracks[language]:
                        if track.get("ext") != "srt":
                            continue
                        content = track.get("data")
                        if content is None and track.get("url"):
                            with ydl.urlopen(track["url"]) as response:
                                content = response.read().decode("utf-8-sig")
                        if not isinstance(content, str):
                            continue
                        text = srt_to_text(content)
                        if text:
                            return SubtitleResult(
                                text=text, content=content, language=language, title=info.get("title"),
                                metadata={"title": info.get("title"), "uploader": info.get("uploader"),
                                          "duration": info.get("duration"), "id": info.get("id"),
                                          "webpage_url": info.get("webpage_url") or source.url},
                            )
                if logger.login_required:
                    raise SubtitleLookupError(
                        "Bilibili subtitles require login. Run bili2text login, or configure "
                        "B2T_COOKIE_FILE / workspace cookies.txt; video download was skipped."
                    )
                if logger.lookup_failed or any(language.lower() != "danmaku" and entries for language, entries in tracks.items()):
                    raise SubtitleLookupError("Bilibili subtitles could not be read; video download was skipped.")
        except Exception as error:
            if progress is not None:
                progress.running("checking_subtitles", message="subtitles_failed", stage_progress=1.0)
            if isinstance(error, SubtitleLookupError):
                raise
            raise SubtitleLookupError(
                f"Bilibili subtitle check failed ({type(error).__name__}); video download was skipped."
            ) from None
        if progress is not None:
            progress.running("checking_subtitles", message="subtitles_unavailable", stage_progress=1.0)
        return None

    def output_path(self, source: SourceRef, settings: Settings) -> Path:
        suffix = f".p{source.page:02d}" if source.page else ""
        return settings.downloads_dir / f"{source.bv}{suffix}.mp4"

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress=None,
    ) -> DownloadResult:
        if source.kind != "bilibili":
            raise ValueError("yt-dlp downloader only supports bilibili sources")

        settings.ensure_directories()

        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise RuntimeError(
                "yt-dlp is not installed. Run `uv sync` to install the core dependencies."
            ) from exc

        ydl_opts = self._build_ydl_opts(source, settings)
        if progress is not None:
            def progress_hook(data: dict[str, Any]) -> None:
                status = data.get("status")
                if status == "downloading":
                    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                    downloaded = data.get("downloaded_bytes") or 0
                    stage_progress = (downloaded / total) if total else None
                    progress.running(
                        "downloading",
                        message="downloading",
                        stage_progress=stage_progress,
                        indeterminate=stage_progress is None,
                    )
                elif status == "finished":
                    progress.running("downloading", message="download_finished", stage_progress=1.0)
            ydl_opts["progress_hooks"] = [progress_hook]
            ydl_opts["noprogress"] = False

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(source.url or f"https://www.bilibili.com/video/{source.bv}", download=True)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            info = ydl.sanitize_info(info)

            video_path = self._resolve_video_path(ydl, info)
            if not video_path.exists():
                raise RuntimeError(f"yt-dlp reported success but no file was found at {video_path}")

        return DownloadResult(
            source=source,
            video_path=video_path,
            title=info.get("title"),
            webpage_url=info.get("webpage_url") or source.url,
            metadata={
                "title": info.get("title"),
                "uploader": info.get("uploader"),
                "duration": info.get("duration"),
                "id": info.get("id"),
                "webpage_url": info.get("webpage_url") or source.url,
            },
        )

    def _build_ydl_opts(self, source: SourceRef, settings: Settings) -> dict[str, Any]:
        ydl_opts: dict[str, Any] = {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "outtmpl": str(settings.downloads_dir / "%(id)s.%(ext)s"),
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "http_chunk_size": 8 * 1024 * 1024,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 3,
            "socket_timeout": 30,
            "http_headers": {
                "Referer": "https://www.bilibili.com/",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        }

        # Support cookies for authenticated access to Bilibili.
        # Priority: B2T_COOKIE_FILE env var > cookies.txt in workspace.
        cookie_path = settings.cookie_path
        if cookie_path.exists():
            ydl_opts["cookiefile"] = str(cookie_path)

        # Bilibili's CDN frequently blocks proxy/VPN nodes, causing 412
        # or SSL errors. Direct connections usually work better.
        # Set B2T_USE_PROXY=1 to re-enable the system proxy if needed.
        use_proxy = os.getenv("B2T_USE_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}
        if not use_proxy:
            ydl_opts["proxy"] = ""

        if source.page is not None:
            ydl_opts["playlist_items"] = str(source.page)
            ydl_opts["noplaylist"] = False
            ydl_opts["outtmpl"] = str(self.output_path(source, settings).with_suffix(".%(ext)s"))
        return ydl_opts

    def _resolve_video_path(self, ydl: Any, info: dict[str, Any]) -> Path:
        requested_downloads = info.get("requested_downloads") or []
        for requested in requested_downloads:
            filepath = requested.get("filepath")
            if filepath:
                return Path(filepath)

        prepared = Path(ydl.prepare_filename(info))
        if prepared.exists():
            return prepared

        merged_mp4 = prepared.with_suffix(".mp4")
        if merged_mp4.exists():
            return merged_mp4

        return prepared


class _SubtitleLogger:
    def __init__(self):
        self.login_required = False
        self.lookup_failed = False

    def debug(self, message):
        pass

    def warning(self, message):
        message = message.lower()
        if "subtitles are only available when logged in" in message:
            self.login_required = True
        if "unable to download" in message or "failed to download" in message:
            self.lookup_failed = True

    def error(self, message):
        pass
