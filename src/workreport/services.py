"""백그라운드 서비스 묶음 (Qt 비의존). UI 와 트레이는 이 객체만 바라본다."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .analysis.captioner import make_caption_fn
from .analysis.claude_client import ClaudeService, has_credentials, make_client
from .analysis.summarizer import apply_draft, fallback_draft, generate_draft
from .collector.base import PlatformProbe, default_probe
from .collector.screenshot import ScreenshotService
from .collector.tracker import ActivityTracker
from .config import Settings, load_settings, save_settings
from .db import Database
from .meeting.detect import MeetingDetector
from .meeting.pipeline import MeetingPipeline
from .meeting.recorder import MeetingRecorder
from .meeting.recorder_watch import RecorderFolderWatcher
from .meeting.stt.base import Transcriber, create_transcriber
from .models import DailyReport, Meeting

log = logging.getLogger(__name__)


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self._path = path
        self._settings = load_settings(path)
        self.listeners: list[Callable[[Settings], None]] = []

    def get(self) -> Settings:
        return self._settings

    def update(self, new: Settings) -> None:
        save_settings(new, self._path)
        self._settings = new
        for listener in list(self.listeners):
            listener(new)

    def persist(self, settings: Settings) -> None:
        save_settings(settings, self._path)


class Services:
    def __init__(
        self,
        store: SettingsStore,
        db: Database,
        probe: PlatformProbe | None = None,
        transcriber_factory: Callable[[Settings], Transcriber] = create_transcriber,
        claude_client_factory: Callable = make_client,
        claude_configured: Callable[[], bool] = has_credentials,
        recorder_backend=None,
    ):
        self.store = store
        self.db = db
        self.probe = probe or default_probe()
        self.claude = ClaudeService(store.get, client_factory=claude_client_factory, configured=claude_configured)
        self.tracker = ActivityTracker(db, self.probe, store.get)
        self.screenshots = ScreenshotService(
            db, self.probe, store.get, caption_fn=lambda: make_caption_fn(self.claude) if self.claude.available() else None
        )
        self.pipeline = MeetingPipeline(db, store.get, self.claude, transcriber_factory=transcriber_factory)
        self.watcher = RecorderFolderWatcher(db, store.get, self._on_recorder_file, persist_settings=store.persist)
        self.recorder = MeetingRecorder(store.get, backend=recorder_backend)
        self.detector = MeetingDetector(
            store.get,
            foreground=lambda: self.tracker.last_window,
            is_recording=lambda: self.recorder.is_recording,
            on_detect=self._on_meeting_detected,
        )
        self.meeting_detected_listeners: list[Callable[[str], None]] = []

    # ------------------------------------------------------------ 수명
    def start(self) -> None:
        self.tracker.start()
        self.screenshots.start()
        self.pipeline.start()
        self.watcher.start()
        self.detector.start()

    def stop(self) -> None:
        if self.recorder.is_recording or self.recorder.needs_finalize:
            try:
                self.stop_recording()
            except Exception:
                log.exception("종료 중 녹음 정리 실패")
        for service in (self.detector, self.watcher, self.screenshots, self.tracker, self.pipeline):
            try:
                service.stop()
            except Exception:
                log.exception("서비스 종료 실패: %s", service)

    # ------------------------------------------------------------ 일시정지
    @property
    def paused(self) -> bool:
        return self.tracker.paused

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.tracker.pause()
        else:
            self.tracker.resume()
        self.screenshots.paused = paused

    # ------------------------------------------------------------ 회의
    def _on_recorder_file(self, path: Path) -> None:
        self.pipeline.import_file(path, "recorder_app")

    def _on_meeting_detected(self, title_hint: str) -> None:
        for listener in list(self.meeting_detected_listeners):
            listener(title_hint)

    def start_recording(self, title_hint: str = "") -> Path:
        return self.recorder.start(title_hint)

    def stop_recording(self) -> Meeting:
        path, started, ended = self.recorder.stop()
        return self.pipeline.import_file(path, "in_app", started_at=started, ended_at=ended, title=self.recorder.title_hint)

    def import_audio(self, path: Path) -> Meeting:
        return self.pipeline.import_file(Path(path), "import")

    # ------------------------------------------------------------ 업무일지
    def generate_report_draft(self, day: str) -> DailyReport:
        """Claude(없으면 통계)로 초안을 만들어 저장한다. 워커 스레드에서 호출."""
        settings = self.store.get()
        draft = generate_draft(self.claude, self.db, day, settings) if self.claude.available() else fallback_draft(self.db, day, settings)
        report = apply_draft(draft, day, self.db.get_report(day))
        self.db.save_report(report)
        return report
