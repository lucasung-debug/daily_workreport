"""오늘 탭: 근무 개요, 앱별 사용 시간, 시간대별 타임라인, 최근 창 기록, 오늘 회의."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..analysis.aggregate import clean_title, summarize_day
from ..models import MeetingStatus
from ..report.render import format_duration, hm
from ..services import Services
from .common import ro_item

RECENT_LIMIT = 60


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class TodayView(QWidget):
    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.lbl_detail = QLabel()
        self.lbl_detail.setStyleSheet("color: #6b7280;")

        self.apps = _table(["앱", "사용 시간", ""])
        self.apps.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.timeline = _table(["시간대", "주요 작업"])
        self.recent = _table(["시작", "끝", "앱", "창 제목"])
        self.meetings = _table(["시간", "회의", "상태"])

        top = QVBoxLayout()
        top.addWidget(self.lbl_status)
        top.addWidget(self.lbl_detail)

        grid = QGridLayout()
        for (title, widget), (r, c) in zip(
            (("앱별 사용 시간", self.apps), ("시간대별 타임라인", self.timeline), ("최근 창 기록", self.recent), ("오늘 회의", self.meetings)),
            ((0, 0), (0, 1), (1, 0), (1, 1)),
        ):
            box = QGroupBox(title)
            QVBoxLayout(box).addWidget(widget)
            grid.addWidget(box, r, c)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(grid, 1)

        self._pending = False
        self.timer = QTimer(self)
        self.timer.setInterval(15_000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def schedule_refresh(self) -> None:
        """잦은 활동 신호를 모아 3초에 한 번만 다시 그린다."""
        if not self._pending:
            self._pending = True
            QTimer.singleShot(3000, self._do_scheduled)

    def _do_scheduled(self) -> None:
        self._pending = False
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        day = date.today().isoformat()
        summary = summarize_day(self.services.db, day)
        state = "기록 일시정지" if self.services.paused else "기록 중"
        if self.services.recorder.is_recording:
            state += " · ● 회의 녹음 중"
        self.lbl_status.setText(f"{day} — {state}")
        if summary.first_ts:
            self.lbl_detail.setText(
                f"첫 활동 {hm(summary.first_ts)} · 마지막 활동 {hm(summary.last_ts)} · "
                f"PC 활동 {format_duration(summary.active_sec)} · 자리 비움 {format_duration(summary.idle_sec)} · 회의 {len(summary.meetings)}건"
            )
        else:
            self.lbl_detail.setText("아직 오늘 기록이 없습니다.")

        top = summary.apps[:20]
        longest = top[0].seconds if top else 1
        self.apps.setRowCount(len(top))
        for row, usage in enumerate(top):
            self.apps.setItem(row, 0, ro_item(usage.app_name))
            self.apps.setItem(row, 1, ro_item(format_duration(usage.seconds), align_right=True))
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setMaximumHeight(10)
            bar.setValue(int(usage.seconds / longest * 100))
            self.apps.setCellWidget(row, 2, bar)

        self.timeline.setRowCount(len(summary.timeline))
        for row, block in enumerate(summary.timeline):
            self.timeline.setItem(row, 0, ro_item(f"{hm(block.start_ts)}~{hm(block.end_ts)}"))
            item = ro_item(block.label)
            if block.is_meeting:
                item.setForeground(Qt.darkBlue)
            self.timeline.setItem(row, 1, item)

        sessions = [s for s in self.services.db.sessions_for_date(day) if s.duration >= 1][-RECENT_LIMIT:][::-1]
        self.recent.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            self.recent.setItem(row, 0, ro_item(hm(s.start_ts)))
            self.recent.setItem(row, 1, ro_item(hm(s.end_ts)))
            self.recent.setItem(row, 2, ro_item(s.app_name))
            self.recent.setItem(row, 3, ro_item(clean_title(s.window_title, s.app_name)))

        self.meetings.setRowCount(len(summary.meetings))
        for row, m in enumerate(summary.meetings):
            self.meetings.setItem(row, 0, ro_item(f"{hm(m.started_at)}~{hm(m.ended_at)}"))
            self.meetings.setItem(row, 1, ro_item(m.title))
            label = MeetingStatus.LABELS.get(m.status, m.status)
            if m.status == MeetingStatus.TRANSCRIBING:
                label += f" {int(m.progress * 100)}%"
            self.meetings.setItem(row, 2, ro_item(label))
