"""메인 창: 오늘 | 업무일지 | 회의록 | 기록 조회 | 설정."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from .. import __version__
from ..services import Services
from .common import app_icon
from .history_view import HistoryView
from .meeting_view import MeetingsView
from .report_editor import ReportEditor
from .settings_view import SettingsView
from .timeline_view import TodayView


class MainWindow(QMainWindow):
    TAB_TODAY, TAB_REPORT, TAB_MEETINGS, TAB_HISTORY, TAB_SETTINGS = range(5)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.quitting = False
        self.setWindowTitle(f"WorkReport {__version__} — 업무일지 · 회의록")
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)

        self.today = TodayView(services)
        self.report = ReportEditor(services)
        self.meetings = MeetingsView(services)
        self.history = HistoryView(services)
        self.settings = SettingsView(services)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.today, "오늘")
        self.tabs.addTab(self.report, "업무일지")
        self.tabs.addTab(self.meetings, "회의록")
        self.tabs.addTab(self.history, "기록 조회")
        self.tabs.addTab(self.settings, "설정")
        self.tabs.currentChanged.connect(self._on_tab)
        self.setCentralWidget(self.tabs)

        for view in (self.report, self.meetings):
            view.status_message.connect(self.show_status)
        self.history.open_report.connect(self.open_report)
        self.show_status("준비됨")

    def show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 15000)

    def open_report(self, day: str) -> None:
        self.tabs.setCurrentIndex(self.TAB_REPORT)
        self.report.open_date(day)
        self.bring_to_front()

    def open_meeting(self, meeting_id: int) -> None:
        self.tabs.setCurrentIndex(self.TAB_MEETINGS)
        self.meetings.select_meeting(meeting_id)
        self.bring_to_front()

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tab(self, index: int) -> None:
        if index == self.TAB_TODAY:
            self.today.refresh()
        elif index == self.TAB_HISTORY:
            self.history.refresh()

    def closeEvent(self, event) -> None:
        if self.quitting:
            event.accept()
            return
        event.ignore()  # 창을 닫아도 트레이에서 계속 기록한다
        self.hide()
