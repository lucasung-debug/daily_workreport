"""메인 창: 왼쪽 사이드바(내비게이션·기록 상태·녹음) + 페이지 + 토스트."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..models import format_hms
from ..services import Services
from .common import app_icon
from .dashboard_view import DashboardView
from .history_view import HistoryView
from .icons import bind_icon
from .meeting_view import MeetingsView
from .report_editor import ReportEditor
from .settings_view import SettingsView
from .theme import font, on_theme_change, tokens
from .widgets import ColorDot, ToastHost, ToggleSwitch, button, hbox, make_label, set_variant, vbox

NAV = [
    ("today", "오늘", "home"),
    ("report", "업무일지", "report"),
    ("meetings", "회의록", "mic"),
    ("history", "기록", "calendar"),
    ("settings", "설정", "settings"),
]


class AppLogo(QWidget):
    def __init__(self, size: int = 32, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        on_theme_change(self, self.update)

    def paintEvent(self, _event) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.accent))
        r = QRectF(0, 0, self.width(), self.height())
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(font(int(self.height() * 0.55), QFont.Bold))
        p.drawText(r, Qt.AlignCenter, "W")
        p.end()


class Sidebar(QFrame):
    navigate = Signal(str)
    toggle_recording = Signal()

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.setObjectName("Sidebar")
        self.setFixedWidth(232)

        brand = hbox(AppLogo(32), vbox(make_label("WorkReport", "brand"), make_label("업무일지 · 회의록", "caption"), spacing=0), spacing=10)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.nav: dict[str, QToolButton] = {}
        nav_layout = vbox(spacing=2)
        for key, label, icon_name in NAV:
            btn = QToolButton()
            btn.setObjectName("NavButton")
            btn.setText(f"  {label}")
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(40)
            btn.setCursor(Qt.PointingHandCursor)
            bind_icon(btn, icon_name, "text2", 18, on_role="accent_text")
            btn.clicked.connect(lambda _c=False, k=key: self.navigate.emit(k))
            self.group.addButton(btn)
            self.nav[key] = btn
            nav_layout.addWidget(btn)

        # 하단 상태 카드
        self.status = QFrame()
        self.status.setObjectName("SidebarStatus")
        self.track_dot = ColorDot(tokens().success, 8)
        self.track_label = make_label("활동 기록", None)
        self.track_label.setStyleSheet("font-weight: 600;")
        self.track_switch = ToggleSwitch(True)
        self.track_switch.setToolTip("끄면 활동 기록을 잠시 멈춰요")
        self.track_switch.toggled.connect(lambda on: self.services.set_paused(not on))
        self.track_caption = make_label("", "caption")
        self.record_btn = button("회의 녹음", "record", "record", on_click=self.toggle_recording.emit)
        self.record_btn.setMinimumHeight(38)
        self.status.setLayout(
            vbox(
                hbox(self.track_dot, self.track_label, None, self.track_switch, spacing=8),
                self.track_caption,
                6,
                self.record_btn,
                spacing=4,
                margins=(14, 12, 14, 14),
            )
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(0)
        layout.addLayout(brand)
        layout.addSpacing(22)
        layout.addLayout(nav_layout)
        layout.addStretch(1)
        layout.addWidget(self.status)
        layout.addSpacing(10)
        layout.addWidget(make_label(f"v{__version__}", "caption"), 0, Qt.AlignHCenter)
        on_theme_change(self, self.refresh_state)
        self.refresh_state()

    def set_current(self, key: str) -> None:
        self.nav[key].setChecked(True)

    def refresh_state(self) -> None:
        t = tokens()
        paused = self.services.paused
        self.track_switch.blockSignals(True)
        self.track_switch.setChecked(not paused)
        self.track_switch.blockSignals(False)
        self.track_dot.color = t.text3 if paused else t.success
        self.track_dot.update()
        self.track_caption.setText("일시정지됨 · 기록하지 않아요" if paused else "앱·창 제목·사용 시간 기록 중")
        rec = self.services.recorder
        if rec.is_recording:
            self.record_btn.setText(f"녹음 중지  {format_hms(rec.elapsed())}")
            set_variant(self.record_btn, "recording", "stop")
        else:
            self.record_btn.setText("회의 녹음")
            set_variant(self.record_btn, "record", "record")


class MainWindow(QMainWindow):
    PAGES = [key for key, _label, _icon in NAV]

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.quitting = False
        self.setWindowTitle("WorkReport")
        self.setWindowIcon(app_icon())
        self.resize(1360, 880)
        self.setMinimumSize(1080, 680)

        self.sidebar = Sidebar(services)
        self.today = DashboardView(services)
        self.report = ReportEditor(services)
        self.meetings = MeetingsView(services)
        self.history = HistoryView(services)
        self.settings = SettingsView(services)

        self.stack = QStackedWidget()
        self.pages = {"today": self.today, "report": self.report, "meetings": self.meetings, "history": self.history, "settings": self.settings}
        for key in self.PAGES:
            self.stack.addWidget(self.pages[key])

        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.toast = ToastHost(root)

        self.sidebar.navigate.connect(self.go)
        self.sidebar.toggle_recording.connect(self.toggle_recording)
        self.today.request_draft.connect(self.write_today_report)
        self.today.open_meeting.connect(self.open_meeting)
        self.meetings.record_requested.connect(self.toggle_recording)
        self.history.open_report.connect(self.open_report)
        for view in (self.today, self.report, self.meetings, self.settings):
            view.status_message.connect(self.show_status)

        self.state_timer = QTimer(self)
        self.state_timer.setInterval(1000)
        self.state_timer.timeout.connect(self.refresh_recording_state)
        self.state_timer.start()
        self.go("today")

    # ------------------------------------------------------------ 이동
    def current_page(self) -> str:
        return self.PAGES[self.stack.currentIndex()]

    def go(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self.sidebar.set_current(key)
        if key == "today":
            self.today.refresh()
        elif key == "report":
            self.report.reload_side()
        elif key == "history":
            self.history.refresh()

    def open_report(self, day: str) -> None:
        self.go("report")
        self.report.open_date(day)
        self.bring_to_front()

    def open_meeting(self, meeting_id: int) -> None:
        self.go("meetings")
        self.meetings.select_meeting(meeting_id)
        self.bring_to_front()

    def write_today_report(self) -> None:
        self.open_report(date.today().isoformat())
        self.report.generate()

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ 알림·녹음
    def show_status(self, message: str, kind: str = "info") -> None:
        self.toast.show(message, kind)

    def refresh_recording_state(self) -> None:
        rec = self.services.recorder
        if rec.needs_finalize:  # 장치 오류로 녹음이 멈춤 → 지금까지 녹음된 부분을 회의로 등록
            self.toggle_recording()
            return
        self.sidebar.refresh_state()
        self.meetings.refresh_record_button()

    def toggle_recording(self, title_hint: str = "") -> None:
        rec = self.services.recorder
        try:
            if rec.is_recording or rec.needs_finalize:
                meeting = self.services.stop_recording()
                self.show_status(f"녹음을 마쳤어요. ‘{meeting.title}’ 음성 변환을 시작합니다.", "success")
                self.meetings.refresh_list()
                self.meetings.select_meeting(meeting.id)
            else:
                self.services.start_recording(title_hint if isinstance(title_hint, str) else "")
                self.show_status("회의 녹음을 시작했어요. 참석자에게 녹음 사실을 알려 주세요.", "info")
        except Exception as exc:
            QMessageBox.warning(self, "녹음 오류", f"녹음을 처리하지 못했어요.\n{exc}")
        self.sidebar.refresh_state()
        self.meetings.refresh_record_button()

    # ------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.toast.frame.isVisible():
            self.toast.reposition()

    def closeEvent(self, event) -> None:
        if self.quitting:
            event.accept()
            return
        event.ignore()  # 창을 닫아도 트레이에서 계속 기록한다
        self.hide()
