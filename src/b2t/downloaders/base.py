from __future__ import annotations

from abc import ABC, abstractmethod

from b2t.config import Settings
from b2t.models import DownloadResult, SourceRef, SubtitleResult
from b2t.progress import ProgressReporter


class Downloader(ABC):
    name = "downloader"

    def fetch_subtitle(
        self, source: SourceRef, settings: Settings, *, progress: ProgressReporter | None = None,
    ) -> SubtitleResult | None:
        return None

    @abstractmethod
    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: ProgressReporter | None = None,
    ) -> DownloadResult:
        raise NotImplementedError
