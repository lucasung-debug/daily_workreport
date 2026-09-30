"""기록 조회 탭: 달력에서 날짜를 골라 지난 업무일지·회의록을 본다."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget, QHBoxLayout, QLabel, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget

from ..analysis.aggregate import summarize_day
from ..report.render import minutes_to_markdown, report_to_markdown
from ..services import Services


class HistoryView(QWidget):
    open_report = Signal(str)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.selectionChanged.connect(self._show_selected)
        self.calendar.currentPageChanged.connect(lambda *_: self.mark_dates())
        legend = QLabel("<b>굵게</b>: 업무일지 있음 · <span style='color:#15803d'>초록</span>: 확정 · <span style='color:#2563eb'>파랑</span>: 회의만 있음")
        btn_open = QPushButton("이 날짜 업무일지 편집")
        btn_open.clicked.connect(lambda: self.open_report.emit(self.selected_day()))

        left = QVBoxLayout()
        left.addWidget(self.calendar)
        left.addWidget(legend)
        left.addWidget(btn_open)
        left.addStretch()
        left_w = QWidget()
        left_w.setLayout(left)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)

        splitter = QSplitter()
        splitter.addWidget(left_w)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(1, 1)
        layout = QHBoxLayout(self)
        layout.addWidget(splitter)
        self.refresh()

    def selected_day(self) -> str:
        return self.calendar.selectedDate().toString("yyyy-MM-dd")

    def refresh(self) -> None:
        self.mark_dates()
        self._show_selected()

    def mark_dates(self) -> None:
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())  # 초기화
        meeting_days = {m.date for m in self.services.db.list_meetings(limit=2000)}
        reports = self.services.db.report_dates()
        for day in meeting_days | set(reports):
            fmt = QTextCharFormat()
            if day in reports:
                fmt.setFontWeight(QFont.Bold)
                if reports[day] == "final":
                    fmt.setForeground(QColor("#15803d"))
            else:
                fmt.setForeground(QColor("#2563eb"))
            self.calendar.setDateTextFormat(QDate.fromString(day, "yyyy-MM-dd"), fmt)

    def _show_selected(self) -> None:
        day = self.selected_day()
        db = self.services.db
        parts = []
        report = db.get_report(day)
        summary = summarize_day(db, day)
        if report:
            parts.append(report_to_markdown(report, summary, author=self.services.store.get().user_name))
        elif summary.first_ts:
            parts.append(f"# {day}\n\n업무일지가 아직 없습니다. '이 날짜 업무일지 편집'에서 AI 초안을 만들 수 있습니다.")
        else:
            parts.append(f"# {day}\n\n기록이 없습니다.")
        for meeting in db.meetings_for(day):
            parts.append(minutes_to_markdown(meeting, db.action_items_for(meeting.id)))
        self.preview.setMarkdown("\n\n---\n\n".join(parts))

    def select_today(self) -> None:
        self.calendar.setSelectedDate(QDate.fromString(date.today().isoformat(), "yyyy-MM-dd"))
