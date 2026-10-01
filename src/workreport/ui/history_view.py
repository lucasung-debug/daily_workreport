"""기록: 달력(작성·확정·회의 표시)과 이번 달 요약, 선택한 날짜의 업무일지·회의록 미리보기."""

from __future__ import annotations

import html

from datetime import date

from PySide6.QtCore import QDate, QLocale, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget, QHBoxLayout, QTextBrowser, QToolButton, QVBoxLayout, QWidget

from ..analysis.aggregate import summarize_day
from ..report.render import WEEKDAYS, format_duration, hm
from ..services import Services
from .icons import bind_icon
from .theme import on_theme_change, tokens
from .widgets import Card, discard, Chip, ColorDot, PageHeader, StatCard, button, hbox, make_label, vbox


def _esc(text: str) -> str:
    return html.escape(text or "")


def preview_html(report, summary, meetings, action_items_for, author: str) -> str:
    """선택한 날짜의 업무일지·회의록을 테마 색에 맞춘 HTML 로 만든다."""
    t = tokens()
    css = f"""
    body {{ color: {t.text}; font-size: 13px; }}
    h3 {{ font-size: 15px; font-weight: 600; margin-top: 18px; margin-bottom: 6px; }}
    p {{ margin: 2px 0; }}
    .muted {{ color: {t.text2}; }}
    .caption {{ color: {t.text3}; font-size: 12px; }}
    .title {{ font-weight: 600; }}
    .meeting {{ color: {t.meeting}; font-weight: 600; }}
    td {{ padding: 4px 14px 4px 0; }}
    """
    out = [f"<style>{css}</style>"]
    if report:
        meta = []
        if author:
            meta.append(f"작성자 {_esc(author)}")
        if summary.first_ts:
            meta.append(f"근무 {hm(summary.first_ts)} – {hm(summary.last_ts)} · 활동 {format_duration(summary.active_sec)}")
        if meta:
            out.append(f"<p class='caption'>{' · '.join(meta)}</p>")
        if report.summary:
            out.append(f"<p class='muted' style='margin-top:8px'>{_esc(report.summary)}</p>")
        for heading, items in (("금일 실적", report.accomplishments), ("명일 계획", report.plans), ("이슈 및 협조 요청", report.issues)):
            out.append(f"<h3>{heading}</h3>")
            if not items:
                out.append("<p class='caption'>없음</p>")
                continue
            out.append("<ol style='margin-left:-18px'>")
            for item in items:
                extra = [format_duration(item.time_spent_min * 60)] if item.time_spent_min else []
                if item.category:
                    extra.append(_esc(item.category))
                tail = f" <span class='caption'>{' · '.join(extra)}</span>" if extra else ""
                details = "".join(f"<br><span class='muted'>{_esc(line.lstrip('-• '))}</span>" for line in item.detail.splitlines() if line.strip())
                out.append(f"<li><span class='title'>{_esc(item.title)}</span>{tail}{details}</li>")
            out.append("</ol>")
        if report.memo:
            out.append(f"<h3>비고</h3><p class='muted'>{_esc(report.memo).replace(chr(10), '<br>')}</p>")
    elif summary.first_ts:
        out.append(f"<p class='muted'>이 날은 PC 활동 {format_duration(summary.active_sec)}, 회의 {len(meetings)}건이 기록돼 있어요. ‘업무일지 쓰기’로 AI 초안을 만들 수 있어요.</p>")
    else:
        out.append("<p class='muted'>이 날짜에는 기록이 없어요.</p>")

    if meetings:
        out.append("<h3>회의</h3>")
        for m in meetings:
            out.append(f"<p><span class='meeting'>●</span> <span class='title'>{_esc(m.title)}</span> <span class='caption'>{hm(m.started_at)} – {hm(m.ended_at)} · {format_duration(m.duration_sec)}</span></p>")
            if m.minutes and m.minutes.summary:
                out.append(f"<p class='muted' style='margin-left:16px'>{_esc(m.minutes.summary)}</p>")
            for d in (m.minutes.decisions if m.minutes else []):
                out.append(f"<p style='margin-left:16px'>결정 · {_esc(d)}</p>")
            for a in action_items_for(m.id):
                mark = "☑" if a.done else "☐"
                owner = f" <span class='caption'>{_esc(a.owner)}{' · ~' + _esc(a.due) if a.due else ''}</span>" if a.owner or a.due else ""
                out.append(f"<p style='margin-left:16px'>{mark} {_esc(a.task)}{owner}</p>")
    if summary.apps:
        out.append("<h3>앱별 사용 시간</h3><table>")
        for usage in summary.apps[:8]:
            out.append(f"<tr><td>{_esc(usage.app_name)}</td><td class='muted' align='right'>{format_duration(usage.seconds)}</td></tr>")
        out.append("</table>")
    return "".join(out)


class MarkedCalendar(QCalendarWidget):
    """날짜 아래에 상태 점(확정·초안·회의)을 그리는 달력."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.marks: dict[str, list[str]] = {}
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
        self.setLocale(QLocale(QLocale.Korean, QLocale.SouthKorea))
        self.setFirstDayOfWeek(Qt.Sunday)
        self.setMinimumHeight(320)
        for name, icon_name in (("qt_calendar_prevmonth", "chevron-left"), ("qt_calendar_nextmonth", "chevron-right")):
            btn = self.findChild(QToolButton, name)
            if btn:
                bind_icon(btn, icon_name, "text2", 18)
        on_theme_change(self, self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        t = tokens()
        header = QTextCharFormat()
        header.setForeground(QColor(t.text3))
        header.setBackground(QColor(t.surface))
        header.setFontWeight(QFont.DemiBold)
        self.setHeaderTextFormat(header)
        for day, color in ((Qt.Saturday, t.accent_text), (Qt.Sunday, t.danger)):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            self.setWeekdayTextFormat(day, fmt)
        self.updateCells()

    def paintCell(self, painter: QPainter, rect, qdate: QDate) -> None:  # noqa: N802 - Qt API
        super().paintCell(painter, rect, qdate)
        colors = self.marks.get(qdate.toString("yyyy-MM-dd"))
        if not colors:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        size, gap = 5.0, 3.0
        total = len(colors) * size + (len(colors) - 1) * gap
        x = rect.center().x() - total / 2
        y = rect.bottom() - 9
        for color in colors:
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(x, y, size, size))
            x += size + gap
        painter.restore()


class HistoryView(QWidget):
    open_report = Signal(str)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services

        header = PageHeader("기록", "날짜를 골라 지난 업무일지와 회의록을 확인하세요")

        self.calendar = MarkedCalendar()
        self.calendar.selectionChanged.connect(self._show_selected)
        self.calendar.currentPageChanged.connect(lambda *_: self.refresh_marks())
        self.legend = QHBoxLayout()
        calendar_card = Card()
        calendar_card.body.addWidget(self.calendar)
        calendar_card.body.addLayout(self.legend)

        self.stat_reports = StatCard("이번 달 업무일지", "report", "accent")
        self.stat_meetings = StatCard("이번 달 회의", "users", "meeting")

        left = QWidget()
        left.setFixedWidth(380)
        left.setLayout(vbox(calendar_card, hbox(self.stat_reports, self.stat_meetings, spacing=12), None, spacing=14))

        self.day_title = make_label("", "section")
        self.day_chip = Chip("", "neutral")
        self.btn_open = button("업무일지 열기", "secondary", "report", on_click=lambda: self.open_report.emit(self.selected_day()))
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview_card = Card(fill=True, padding=22)
        self.preview_card.body.addLayout(hbox(self.day_title, self.day_chip, None, self.btn_open))
        self.preview_card.body.addWidget(self.preview, 1)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(left)
        body.addWidget(self.preview_card, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)
        layout.addWidget(header)
        layout.addLayout(body, 1)
        on_theme_change(self, self._build_legend)
        on_theme_change(self, self._show_selected)
        self._build_legend()
        self.refresh()

    def _build_legend(self) -> None:
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        discard(w)
        t = tokens()
        for color, text in ((t.success, "확정"), (t.warning, "초안"), (t.meeting, "회의")):
            self.legend.addLayout(hbox(ColorDot(color, 7), make_label(text, "caption"), spacing=5))
        self.legend.addStretch(1)

    def selected_day(self) -> str:
        return self.calendar.selectedDate().toString("yyyy-MM-dd")

    def refresh(self) -> None:
        self.refresh_marks()
        self._show_selected()

    def refresh_marks(self) -> None:
        t = tokens()
        reports = self.services.db.report_dates()
        meetings: dict[str, int] = {}
        for m in self.services.db.list_meetings(limit=5000):
            meetings[m.date] = meetings.get(m.date, 0) + 1
        marks: dict[str, list[str]] = {}
        for day, status in reports.items():
            marks.setdefault(day, []).append(t.success if status == "final" else t.warning)
        for day in meetings:
            marks.setdefault(day, []).append(t.meeting)
        self.calendar.marks = marks
        self.calendar.updateCells()

        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        prefix = f"{year:04d}-{month:02d}"
        month_reports = [d for d in reports if d.startswith(prefix)]
        finals = sum(1 for d in month_reports if reports[d] == "final")
        self.stat_reports.set(f"{len(month_reports)}일", f"확정 {finals}일")
        month_meetings = sum(c for d, c in meetings.items() if d.startswith(prefix))
        self.stat_meetings.set(f"{month_meetings}건", f"{month}월")

    def _show_selected(self) -> None:
        day = self.selected_day()
        d = date.fromisoformat(day)
        self.day_title.setText(f"{d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})")
        db = self.services.db
        report = db.get_report(day)
        summary = summarize_day(db, day)
        meetings = db.meetings_for(day)
        if report and report.status == "final":
            self.day_chip.set_kind("success", "확정됨")
        elif report:
            self.day_chip.set_kind("warning", "초안")
        else:
            self.day_chip.set_kind("neutral", "업무일지 없음")
        self.btn_open.setText("업무일지 열기" if report else "업무일지 쓰기")

        self.preview.setHtml(preview_html(report, summary, meetings, db.action_items_for, self.services.store.get().user_name))

    def select_today(self) -> None:
        self.calendar.setSelectedDate(QDate.fromString(date.today().isoformat(), "yyyy-MM-dd"))
