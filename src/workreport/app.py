"""애플리케이션 부트스트랩: 로그, 단일 인스턴스, 서비스·창·트레이·스케줄러 연결."""

from __future__ import annotations

import getpass
import logging
import sys
from datetime import date
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLocale, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, paths
from .db import Database
from .scheduler import DailyScheduler
from .services import Services, SettingsStore
from .ui import theme
from .ui.common import Bridge, app_icon, run_async
from .ui.main_window import MainWindow
from .ui.onboarding import OnboardingDialog
from .ui.tray import Tray

log = logging.getLogger(__name__)

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
            on_day_change=self._on_day_change,
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
        b.recorder_error.connect(self._on_recorder_error)
        self.window.settings.theme_changed.connect(lambda mode: theme.apply_theme(self.qapp, mode))
        self.window.meetings.open_settings.connect(self.open_ai_settings)

    # ------------------------------------------------------------
    def _on_day_change(self, _day: str) -> None:
        self.window.sync_todos()  # 어제 업무일지의 명일 계획 → 오늘 할 일
        self.window.today.refresh()
        self.window.history.refresh()
        self.window.refresh_todo_count()

    def toggle_recording(self, title_hint: str = "") -> None:
        self.window.toggle_recording(title_hint)
        self.tray.refresh()

    def open_ai_settings(self) -> None:
        self.window.go("settings")
        self.window.settings.show_page("ai")
        self.window.bring_to_front()

    def _on_recorder_error(self, message: str) -> None:
        self.tray.notify("녹음 장치 오류", message)
        self.window.show_status(message, "error")

    def _on_meeting_detected(self, title_hint: str) -> None:
        label = f"'{title_hint}' " if title_hint else ""
        self.tray.notify(
            "회의 중인가요?",
            f"{label}회의가 감지되었습니다. 여기를 누르면 녹음을 시작합니다. (참석자에게 녹음 사실을 알려 주세요)",
            on_click=lambda: None if self.services.recorder.is_recording else self.toggle_recording(title_hint),
        )

    def _on_pipeline_notify(self, title: str, message: str, meeting_id: int) -> None:
        self.tray.notify(title, message, on_click=lambda: self.window.open_meeting(meeting_id))

    def auto_draft(self, day: str, manual: bool = False) -> None:
        def done(_report) -> None:
            self.window.report.reload_if_idle(day)
            self.tray.notify("업무일지 초안이 준비되었어요", "확인하고 다듬은 뒤 확정하세요.", on_click=lambda: self.window.open_report(day))

        def failed(message: str) -> None:
            self.tray.notify("업무일지 초안을 만들지 못했어요", message, on_click=lambda: self.window.open_report(day))

        if manual:
            self.window.write_today_report()
            return
        run_async(self.services.generate_report_draft, day, on_done=done, on_error=failed)

    def show_onboarding_if_needed(self) -> None:
        if not self.services.store.get().onboarding_done:
            OnboardingDialog(self.services.store, self.window if self.window.isVisible() else None).exec()
            self.window.settings.load()
            self.window.today.refresh()

    def quit(self) -> None:
        if self.services.recorder.is_recording:
            answer = QMessageBox.question(None, "종료", "회의를 녹음 중입니다. 녹음을 저장하고 종료할까요?")
            if answer != QMessageBox.Yes:
                return
        self.window.report.flush()
        self.window.meetings.flush()
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
    QLocale.setDefault(QLocale(QLocale.Korean, QLocale.SouthKorea))

    if _notify_running_instance():
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(_server_name())
    server.listen(_server_name())

    store = SettingsStore()
    theme.setup_application(qapp, store.get().theme)
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
