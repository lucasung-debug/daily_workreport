"""애플리케이션 부트스트랩: 로그, 단일 인스턴스, 서비스·창·트레이·스케줄러 연결."""

from __future__ import annotations

import getpass
import logging
import sys
from datetime import date
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, paths
from .db import Database
from .scheduler import DailyScheduler
from .services import Services, SettingsStore
from .ui.common import Bridge, app_icon, run_async
from .ui.main_window import MainWindow
from .ui.tray import Tray

log = logging.getLogger(__name__)

ONBOARDING_TEXT = """\
WorkReport 는 이 PC 에서 다음을 기록합니다.

• 활성 창의 앱 이름·창 제목·사용 시간 (제외 목록에 있는 앱·키워드는 제목을 가립니다)
• 회의 녹음 (Windows 녹음기 앱 저장 폴더 감시, 또는 앱의 ● 녹음 버튼)

기록은 이 PC 의 SQLite DB 에만 저장됩니다. Claude API 키를 설정하면 업무일지 초안과 회의록 작성을 위해 \
집계된 활동 요약과 회의 전사문이 Anthropic API 로 전송됩니다. 로컬 Whisper 를 쓰면 음성은 PC 밖으로 나가지 않습니다.

회의를 녹음할 때는 반드시 참석자에게 녹음 사실을 알리고 동의를 받으세요.

먼저 [설정] 탭에서 이름, 근무시간, Claude API 키를 입력하세요."""


def setup_logging() -> None:
    handler = RotatingFileHandler(paths.log_dir() / "workreport.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if not getattr(sys, "frozen", False):
        root.addHandler(logging.StreamHandler())


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"{APP_NAME}-{user}"


def _notify_running_instance() -> bool:
    """이미 실행 중이면 그 창을 띄우고 True."""
    socket = QLocalSocket()
    socket.connectToServer(_server_name())
    if socket.waitForConnected(500):
        socket.write(b"show")
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True
    return False


class Application:
    def __init__(self, qapp: QApplication, services: Services, start_minimized: bool = False):
        self.qapp = qapp
        self.services = services
        self.bridge = Bridge()
        self.window = MainWindow(services)
        self.tray = Tray(
            services,
            on_open=self.window.bring_to_front,
            on_toggle_recording=self.toggle_recording,
            on_generate=lambda: self.auto_draft(date.today().isoformat(), manual=True),
            on_quit=self.quit,
        )
        self._wire()
        self.scheduler = DailyScheduler(
            services.store.get,
            services.db,
            on_auto_draft=self.auto_draft,
            on_day_change=lambda _day: (self.window.today.refresh(), self.window.history.refresh()),
            on_daily_cleanup=lambda: run_async(services.pipeline.cleanup_old_audio),
        )
        self.timer = QTimer()
        self.timer.setInterval(30_000)
        self.timer.timeout.connect(self.scheduler.tick)
        self.timer.start()
        self.tray.show()
        if not start_minimized:
            self.window.show()

    def _wire(self) -> None:
        s, b = self.services, self.bridge
        # 백그라운드 스레드 → UI 스레드
        s.pipeline.listeners.append(b.meeting_changed.emit)
        s.pipeline.notify = b.notify.emit
        s.tracker.listeners.append(b.activity_changed.emit)
        s.meeting_detected_listeners.append(b.meeting_detected.emit)
        s.recorder.on_error = b.recorder_error.emit

        b.meeting_changed.connect(self.window.meetings.on_meeting_changed)
        b.meeting_changed.connect(lambda _id: self.window.today.schedule_refresh())
        b.activity_changed.connect(self.window.today.schedule_refresh)
        b.notify.connect(self._on_pipeline_notify)
        b.meeting_detected.connect(self._on_meeting_detected)
        b.recorder_error.connect(lambda msg: self.tray.notify("녹음 장치 오류", msg))
        self.window.meetings.recording_changed.connect(lambda _rec: self.tray.refresh())

    # ------------------------------------------------------------
    def toggle_recording(self, title_hint: str = "") -> None:
        self.window.meetings.toggle_recording(title_hint)
        self.tray.refresh()

    def _on_meeting_detected(self, title_hint: str) -> None:
        label = f"'{title_hint}' " if title_hint else ""
        self.tray.notify(
            "회의 중인가요?",
            f"{label}회의가 감지되었습니다. 여기를 누르면 녹음을 시작합니다. (참석자에게 녹음 사실을 알려 주세요)",
            on_click=lambda: None if self.services.recorder.is_recording else self.toggle_recording(title_hint),
        )

    def _on_pipeline_notify(self, title: str, message: str) -> None:
        meeting_id = self.services.pipeline.current
        self.tray.notify(title, message, on_click=(lambda: self.window.open_meeting(meeting_id)) if meeting_id else self.window.bring_to_front)

    def auto_draft(self, day: str, manual: bool = False) -> None:
        def done(_report) -> None:
            if self.window.report.day == day and not self.window.report._dirty:
                self.window.report.load(day)
            self.tray.notify("업무일지 초안이 준비되었습니다", f"{day} 초안을 확인하고 수정·확정하세요.", on_click=lambda: self.window.open_report(day))

        def failed(message: str) -> None:
            self.tray.notify("업무일지 초안 생성 실패", message, on_click=lambda: self.window.open_report(day))

        if manual:
            self.window.open_report(day)
            self.window.report.generate()
            return
        run_async(self.services.generate_report_draft, day, on_done=done, on_error=failed)

    def show_onboarding_if_needed(self) -> None:
        settings = self.services.store.get()
        if not settings.onboarding_done:
            QMessageBox.information(self.window if self.window.isVisible() else None, "WorkReport 시작하기", ONBOARDING_TEXT)
            settings.onboarding_done = True
            self.services.store.persist(settings)

    def quit(self) -> None:
        if self.services.recorder.is_recording:
            answer = QMessageBox.question(None, "종료", "회의를 녹음 중입니다. 녹음을 저장하고 종료할까요?")
            if answer != QMessageBox.Yes:
                return
        if not self.window.report.confirm_discard():
            return
        self.window.quitting = True
        self.timer.stop()
        self.tray.hide()
        self.services.stop()
        self.qapp.quit()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    setup_logging()
    qapp = QApplication(argv)
    qapp.setApplicationName(APP_NAME)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(app_icon())

    if _notify_running_instance():
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(_server_name())
    server.listen(_server_name())

    store = SettingsStore()
    db = Database(paths.db_path())
    services = Services(store, db)
    app = Application(qapp, services, start_minimized="--minimized" in argv)

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn:
            conn.readyRead.connect(app.window.bring_to_front)

    server.newConnection.connect(on_connection)
    services.start()
    QTimer.singleShot(500, app.show_onboarding_if_needed)
    log.info("WorkReport 시작")
    code = qapp.exec()
    db.close()
    return code
