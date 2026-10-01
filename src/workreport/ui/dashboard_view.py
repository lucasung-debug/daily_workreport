"""오늘(대시보드): 인사·핵심 지표·하루 타임라인·앱별 사용 시간·오늘 회의·빠른 메모·최근 활동."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ..analysis.aggregate import DaySummary, clean_title, summarize_day
from ..db import day_bounds
from ..models import ActivitySession, Meeting, MeetingStatus
from ..report.render import WEEKDAYS, format_duration, hm
from ..services import Services
from .theme import on_theme_change, tokens
from .widgets import (
    Card,
    Chip,
    ColorDot,
    ElidedLabel,
    PageHeader,
    StatCard,
    TimelineBar,
    UsageList,
    button,
    clear_layout,
    hbox,
    make_label,
    scroll_area,
    vbox,
)

MERGE_GAP_SEC = 90
RECENT_LIMIT = 8
TOP_APPS = 7


def greeting(name: str, now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if hour < 11:
        text = "좋은 아침이에요"
    elif hour < 18:
        text = "좋은 오후예요"
    else:
        text = "오늘도 수고 많으셨어요"
    return f"{text}, {name}님" if name else text


def korean_date(day: date) -> str:
    return f"{day.month}월 {day.day}일 {WEEKDAYS[day.weekday()]}요일"


def app_colors(summary: DaySummary) -> dict[str, str]:
    series = tokens().series
    return {usage.app_name: series[i % len(series)] for i, usage in enumerate(summary.apps[:TOP_APPS])}


def other_color() -> str:
    return tokens().border_strong


def timeline_segments(sessions: list[ActivitySession], colors: dict[str, str]) -> list[tuple[float, float, str, str]]:
    """같은 앱이 짧은 간격으로 이어지면 한 구간으로 합친다."""
    merged: list[list] = []
    for s in sessions:
        if s.is_idle or s.duration <= 0:
            continue
        title = clean_title(s.window_title, s.app_name)
        if merged and merged[-1][2] == s.app_name and s.start_ts - merged[-1][1] <= MERGE_GAP_SEC:
            merged[-1][1] = max(merged[-1][1], s.end_ts)
            merged[-1][4] += s.duration
            if title and title not in merged[-1][3]:
                merged[-1][3].append(title)
        else:
            merged.append([s.start_ts, s.end_ts, s.app_name, [title] if title else [], s.duration])
    out = []
    for start, end, app, titles, _dur in merged:
        label = app + ("\n" + "\n".join(f"· {t}" for t in titles[:3]) if titles else "")
        out.append((start, end, colors.get(app, other_color()), label))
    return out


def hour_range(summary: DaySummary, work_start: int, work_end: int) -> tuple[int, int]:
    start, end = work_start, work_end
    if summary.first_ts:
        start = min(start, datetime.fromtimestamp(summary.first_ts).hour)
        last = datetime.fromtimestamp(summary.last_ts)
        end = max(end, last.hour + (1 if last.minute or last.second else 0))
    for m in summary.meetings:
        start = min(start, datetime.fromtimestamp(m.started_at).hour)
        end = max(end, datetime.fromtimestamp(m.ended_at).hour + 1)
    return max(0, start), min(24, max(end, start + 1))


def meeting_chip(m: Meeting) -> tuple[str, str]:
    label = MeetingStatus.LABELS.get(m.status, m.status)
    if m.status == MeetingStatus.TRANSCRIBING:
        label = f"변환 중 {int(m.progress * 100)}%"
    kind = {
        MeetingStatus.DONE: "success",
        MeetingStatus.ERROR: "danger",
        MeetingStatus.TRANSCRIBED: "warning",
        MeetingStatus.QUEUED: "neutral",
    }.get(m.status, "accent")
    return label, kind


class DashboardView(QWidget):
    request_draft = Signal()
    open_meeting = Signal(int)
    status_message = Signal(str, str)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.setObjectName("Page")

        self.header = PageHeader("", "")
        self.btn_draft = button("오늘 업무일지 쓰기", "primary", "sparkles", on_click=self.request_draft.emit)
        self.header.add_action(self.btn_draft)

        self.stat_active = StatCard("PC 활동", "activity", "accent")
        self.stat_top = StatCard("가장 많이 쓴 앱", "monitor", "success")
        self.stat_meetings = StatCard("회의", "users", "meeting")
        self.stat_idle = StatCard("자리 비움", "moon", "warning")

        self.timeline_card = Card("하루 타임라인")
        self.timeline = TimelineBar()
        self.legend = QHBoxLayout()
        self.legend.setSpacing(16)
        self.timeline_card.body.addWidget(self.timeline)
        self.timeline_card.body.addLayout(self.legend)

        self.usage_card = Card("앱별 사용 시간")
        self.usage = UsageList()
        self.usage_empty = make_label("아직 기록된 활동이 없어요. 작업을 시작하면 여기에 쌓여요.", "muted", wrap=True)
        self.usage_card.body.addWidget(self.usage)
        self.usage_card.body.addWidget(self.usage_empty)

        self.meetings_card = Card("오늘 회의")
        self.meetings_box = QVBoxLayout()
        self.meetings_box.setSpacing(6)
        self.meetings_card.body.addLayout(self.meetings_box)

        self.note_card = Card("빠른 메모", "업무일지 초안에 반영돼요")
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("예) 오후에 QA팀에 회귀 테스트 범위 공유")
        self.note_input.returnPressed.connect(self._add_note)
        self.notes_box = QVBoxLayout()
        self.notes_box.setSpacing(4)
        self.note_card.body.addLayout(hbox(self.note_input, button("추가", "secondary", on_click=self._add_note)))
        self.note_card.body.addLayout(self.notes_box)

        self.recent_card = Card("최근 활동")
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        self.recent_card.body.addLayout(self.recent_box)

        body = QWidget()
        stats = hbox(self.stat_active, self.stat_top, self.stat_meetings, self.stat_idle, spacing=14)
        right = vbox(self.meetings_card, self.note_card, spacing=14)
        middle = QHBoxLayout()
        middle.setSpacing(14)
        middle.addWidget(self.usage_card, 3)
        middle.addLayout(right, 2)
        layout = vbox(self.header, stats, self.timeline_card, middle, self.recent_card, None, spacing=16, margins=(32, 28, 32, 28))
        body.setLayout(layout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area(body))

        self._pending = False
        self.timer = QTimer(self)
        self.timer.setInterval(15_000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start()
        on_theme_change(self, self.refresh)
        self.refresh()

    # ------------------------------------------------------------
    def schedule_refresh(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(3000, self._do_scheduled)

    def _do_scheduled(self) -> None:
        self._pending = False
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        today = date.today()
        day = today.isoformat()
        settings = self.services.store.get()
        summary = summarize_day(self.services.db, day)
        colors = app_colors(summary)

        state = "활동 기록 일시정지" if self.services.paused else "활동 기록 중"
        if self.services.recorder.is_recording:
            state += " · 회의 녹음 중"
        self.header.title.setText(greeting(settings.user_name))
        self.header.subtitle.setText(f"{korean_date(today)} · {state}")

        if summary.first_ts:
            self.stat_active.set(format_duration(summary.active_sec), f"{hm(summary.first_ts)} – {hm(summary.last_ts)}")
        else:
            self.stat_active.set("0분", "아직 기록 없음")
        if summary.apps:
            self.stat_top.set(summary.apps[0].app_name, format_duration(summary.apps[0].seconds))
        else:
            self.stat_top.set("—", "")
        meeting_sec = sum(m.duration_sec for m in summary.meetings)
        self.stat_meetings.set(f"{len(summary.meetings)}건", format_duration(meeting_sec) if summary.meetings else "오늘 회의 없음")
        self.stat_idle.set(format_duration(summary.idle_sec), "5분 이상 입력 없음" if summary.idle_sec else "")
        self.stat_top.value.setToolTip(summary.apps[0].app_name if summary.apps else "")

        # 타임라인
        start_hour, end_hour = hour_range(summary, settings.work_start_time().hour, settings.work_end_time().hour + 1)
        sessions = self.services.db.sessions_for_date(day)
        self.timeline.set_data(
            day_bounds(day)[0],
            timeline_segments(sessions, colors),
            [(m.started_at, m.ended_at, m.title) for m in summary.meetings],
            start_hour,
            end_hour,
            show_now=True,
        )
        self.timeline_card.set_caption(f"{hm(summary.first_ts)} – {hm(summary.last_ts)}" if summary.first_ts else "")
        clear_layout(self.legend)
        for usage in summary.apps[:5]:
            self.legend.addLayout(hbox(ColorDot(colors.get(usage.app_name, other_color()), 8), make_label(usage.app_name, "caption"), spacing=6))
        if summary.meetings:
            self.legend.addLayout(hbox(ColorDot(tokens().meeting, 8), make_label("회의", "caption"), spacing=6))
        self.legend.addStretch(1)

        # 앱별 사용 시간
        top = summary.apps[:TOP_APPS]
        longest = top[0].seconds if top else 1
        self.usage.set_rows([(colors.get(u.app_name, other_color()), u.app_name, u.seconds / longest, format_duration(u.seconds)) for u in top])
        self.usage.setVisible(bool(top))
        self.usage_empty.setVisible(not top)

        # 오늘 회의
        clear_layout(self.meetings_box)
        if not summary.meetings:
            self.meetings_box.addWidget(make_label("오늘 회의가 없어요. 녹음기 앱으로 녹음하면 자동으로 들어와요.", "muted", wrap=True))
        for m in summary.meetings:
            label, kind = meeting_chip(m)
            title = ElidedLabel(m.title)
            title.setStyleSheet("font-weight: 600;")
            row = QWidget()
            row.setCursor(Qt.PointingHandCursor)
            row_layout = hbox(vbox(title, make_label(f"{hm(m.started_at)} – {hm(m.ended_at)} · {format_duration(m.duration_sec)}", "caption"), spacing=1), Chip(label, kind), spacing=10, margins=(0, 4, 0, 4))
            row_layout.setStretch(0, 1)
            row.setLayout(row_layout)
            row.mousePressEvent = lambda _e, mid=m.id: self.open_meeting.emit(mid)
            self.meetings_box.addWidget(row)

        # 메모
        clear_layout(self.notes_box)
        for note in self.services.db.notes_for(day)[-4:][::-1]:
            row = hbox(make_label(datetime.fromtimestamp(note.ts).strftime("%H:%M"), "caption"), ElidedLabel(note.text), spacing=10)
            row.setStretch(1, 1)
            self.notes_box.addLayout(row)

        # 최근 활동
        clear_layout(self.recent_box)
        recent = [s for s in sessions if not s.is_idle and s.duration >= 5][-RECENT_LIMIT:][::-1]
        if not recent:
            self.recent_box.addWidget(make_label("기록이 쌓이면 최근 사용한 창이 여기에 보여요.", "muted"))
        for s in recent:
            time_label = make_label(f"{hm(s.start_ts)} – {hm(s.end_ts)}", "caption")
            time_label.setFixedWidth(96)
            app = ElidedLabel(s.app_name, "muted")
            app.setFixedWidth(150)
            row = hbox(time_label, ColorDot(colors.get(s.app_name, other_color()), 8), app, ElidedLabel(clean_title(s.window_title, s.app_name)), spacing=10, margins=(0, 3, 0, 3))
            row.setStretch(3, 1)
            self.recent_box.addLayout(row)

    def _add_note(self) -> None:
        text = self.note_input.text().strip()
        if not text:
            return
        self.services.db.add_note(text)
        self.note_input.clear()
        self.status_message.emit("메모를 저장했어요. 오늘 업무일지 초안에 반영돼요.", "success")
        self.refresh()
