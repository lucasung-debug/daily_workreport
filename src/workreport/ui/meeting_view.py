"""회의록: 회의 목록(검색·상태), 상세(진행 배너·회의록 카드·액션아이템·자동 저장), 채팅형 전사문과 재생."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..models import ActionItem, DiscussionTopic, Meeting, MeetingMinutes, MeetingStatus, format_hms
from ..report.render import WEEKDAYS, format_duration, hm, minutes_to_markdown
from ..services import Services
from .common import run_async
from .icons import bind_icon, themed_icon
from .theme import font, on_theme_change, repolish, tokens
from .todo_view import todo_signals
from .widgets import (
    discard,
    AutoTextEdit,
    Card,
    Chip,
    EmptyState,
    IconBadge,
    PageHeader,
    SegmentedControl,
    button,
    hbox,
    icon_button,
    make_label,
    scroll_area,
    set_variant,
    vbox,
)

AUDIO_FILTER = "오디오 (*.m4a *.mp3 *.wav *.flac *.wma *.aac *.ogg)"
AUTOSAVE_MS = 800
SOURCE_LABELS = {"recorder_app": "녹음기 앱", "in_app": "앱 녹음", "import": "가져온 파일"}
ROLE_ID = Qt.UserRole
ROLE_VIEW = Qt.UserRole + 1


def discussion_to_text(topics: list[DiscussionTopic]) -> str:
    lines = []
    for topic in topics:
        lines.append(f"## {topic.topic}")
        lines += [f"- {p}" for p in topic.points]
    return "\n".join(lines)


def text_to_discussion(text: str) -> list[DiscussionTopic]:
    topics: list[DiscussionTopic] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            topics.append(DiscussionTopic(topic=line.lstrip("# ").strip(), points=[]))
        else:
            if not topics:
                topics.append(DiscussionTopic(topic="논의", points=[]))
            topics[-1].points.append(line.lstrip("-•* ").strip())
    return topics


def _lines(text: str) -> list[str]:
    return [line.lstrip("-•* ").strip() for line in text.splitlines() if line.strip()]


def status_chip(m: Meeting) -> tuple[str, str]:
    if m.status == MeetingStatus.TRANSCRIBING:
        return f"음성 변환 중 {int(m.progress * 100)}%", "accent"
    label = MeetingStatus.LABELS.get(m.status, m.status)
    kind = {MeetingStatus.DONE: "success", MeetingStatus.ERROR: "danger", MeetingStatus.TRANSCRIBED: "warning", MeetingStatus.QUEUED: "neutral"}.get(m.status, "accent")
    return label, kind


def meeting_when(m: Meeting) -> str:
    d = date.fromisoformat(m.date)
    return f"{d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]}) {hm(m.started_at)} – {hm(m.ended_at)}"


def _chip_colors(kind: str) -> tuple[str, str]:
    t = tokens()
    return {
        "success": (t.success_subtle, t.success),
        "danger": (t.danger_subtle, t.danger),
        "warning": (t.warning_subtle, t.warning),
        "accent": (t.accent_subtle, t.accent_text),
    }.get(kind, (t.surface2, t.text2))


# ---------------------------------------------------------------- 회의 목록


class MeetingListDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        view = index.data(ROLE_VIEW) or {}
        return QSize(option.rect.width(), 82 if view.get("progress") is not None else 70)

    def paint(self, painter: QPainter, option, index) -> None:
        t = tokens()
        view = index.data(ROLE_VIEW) or {}
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 3, -2, -3)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        if selected:
            painter.setPen(QColor(t.selection))
            painter.setBrush(QColor(t.accent_subtle))
        elif hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t.surface2))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 10, 10)

        pad = 14
        chip_text, chip_kind = view.get("chip", ("", "neutral"))
        chip_font = font(11, QFont.DemiBold)
        chip_w = QFontMetrics(chip_font).horizontalAdvance(chip_text) + 16
        chip_rect = QRectF(rect.right() - pad - chip_w, rect.top() + 12, chip_w, 20)
        bg, fg = _chip_colors(chip_kind)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(chip_rect, 10, 10)
        painter.setPen(QColor(fg))
        painter.setFont(chip_font)
        painter.drawText(chip_rect, Qt.AlignCenter, chip_text)

        title_font = font(14, QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(t.text))
        title_w = int(chip_rect.left() - rect.left() - pad - 10)
        title = QFontMetrics(title_font).elidedText(view.get("title", ""), Qt.ElideRight, title_w)
        painter.drawText(QRect(int(rect.left() + pad), int(rect.top() + 10), title_w, 22), Qt.AlignVCenter | Qt.AlignLeft, title)

        meta_font = font(12)
        painter.setFont(meta_font)
        painter.setPen(QColor(t.text3))
        meta_w = int(rect.width() - 2 * pad)
        meta = QFontMetrics(meta_font).elidedText(view.get("meta", ""), Qt.ElideRight, meta_w)
        painter.drawText(QRect(int(rect.left() + pad), int(rect.top() + 34), meta_w, 18), Qt.AlignVCenter | Qt.AlignLeft, meta)

        progress = view.get("progress")
        if progress is not None:
            bar = QRectF(rect.left() + pad, rect.bottom() - 14, rect.width() - 2 * pad, 4)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t.surface3))
            painter.drawRoundedRect(bar, 2, 2)
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4.0, bar.width() * progress), 4), 2, 2)
        painter.restore()


# ---------------------------------------------------------------- 전사문


def speaker_color(speaker: str) -> str:
    t = tokens()
    if speaker == "나":
        return t.accent_text
    if not speaker or speaker == "상대방":
        return t.text2
    digits = "".join(ch for ch in speaker if ch.isdigit())
    idx = int(digits) if digits else sum(map(ord, speaker))
    return t.series[1 + idx % (len(t.series) - 1)]


class TranscriptDelegate(QStyledItemDelegate):
    BUBBLE_RATIO = 0.74

    def __init__(self, view: QListWidget):
        super().__init__(view)
        self.view = view

    def _text_rect(self, width: int, text: str) -> QRect:
        max_w = int(max(220, width * self.BUBBLE_RATIO)) - 28
        return QFontMetrics(font(13)).boundingRect(QRect(0, 0, max_w, 100000), Qt.TextWordWrap, text)

    def sizeHint(self, option, index) -> QSize:
        width = self.view.viewport().width()
        rect = self._text_rect(width, index.data(Qt.UserRole + 2) or "")
        return QSize(width, rect.height() + 18 + 20 + 12)

    def paint(self, painter: QPainter, option, index) -> None:
        t = tokens()
        speaker = index.data(Qt.UserRole + 1) or ""
        text = index.data(Qt.UserRole + 2) or ""
        stamp = format_hms(index.data(Qt.UserRole) or 0)
        mine = speaker == "나"
        text_rect = self._text_rect(option.rect.width(), text)
        bubble_w = text_rect.width() + 28
        top = option.rect.top() + 4
        x = option.rect.right() - bubble_w - 12 if mine else option.rect.left() + 12

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        head_font, stamp_font = font(12, QFont.DemiBold), font(11)
        head = speaker or "화자"
        head_w = QFontMetrics(head_font).horizontalAdvance(head)
        stamp_w = QFontMetrics(stamp_font).horizontalAdvance(stamp)
        head_x = x + bubble_w - head_w - stamp_w - 8 if mine else x + 2
        painter.setFont(head_font)
        painter.setPen(QColor(speaker_color(speaker)))
        painter.drawText(QRect(int(head_x), top, head_w + 2, 16), Qt.AlignVCenter, head)
        painter.setFont(stamp_font)
        painter.setPen(QColor(t.text3))
        painter.drawText(QRect(int(head_x + head_w + 8), top, stamp_w + 2, 16), Qt.AlignVCenter, stamp)

        bubble = QRectF(x, top + 20, bubble_w, text_rect.height() + 18)
        selected = bool(option.state & QStyle.State_Selected)
        painter.setPen(QColor(t.accent) if selected else Qt.NoPen)
        painter.setBrush(QColor(t.accent_subtle if mine else t.surface2))
        painter.drawRoundedRect(bubble, 12, 12)
        painter.setPen(QColor(t.text))
        painter.setFont(font(13))
        painter.drawText(bubble.adjusted(14, 9, -14, -9).toRect(), Qt.TextWordWrap, text)
        painter.restore()


class TranscriptView(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(TranscriptDelegate(self))
        self.setResizeMode(QListView.Adjust)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setUniformItemSizes(False)
        on_theme_change(self, self.viewport().update)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.doItemsLayout()


class PlayerBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SubtleCard")
        self._player = None
        self._audio_out = None
        self._path = ""
        self.btn = QToolButton()
        self.btn.setCursor(Qt.PointingHandCursor)
        bind_icon(self.btn, "play", "text", 18)
        self.btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderMoved.connect(lambda v: self._player and self._player.setPosition(v))
        self.time = make_label("", "caption")
        self.setLayout(hbox(self.btn, self.slider, self.time, spacing=10, margins=(10, 6, 14, 6)))

    def set_source(self, path: str) -> None:
        self._path = path if path and Path(path).exists() else ""
        self.setEnabled(bool(self._path))
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl())
        self.slider.setValue(0)
        self.time.setText("00:00:00" if self._path else "녹음 파일 없음")

    def _ensure(self):
        if self._player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._audio_out = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio_out)
            self._player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
            self._player.positionChanged.connect(self._on_position)
            self._player.playbackStateChanged.connect(self._on_state)
        if self._player.source() != QUrl.fromLocalFile(self._path):
            self._player.setSource(QUrl.fromLocalFile(self._path))
        return self._player

    def _on_position(self, pos: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        self.time.setText(f"{format_hms(pos / 1000)} / {format_hms(self._player.duration() / 1000)}")

    def _on_state(self, state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        bind_icon(self.btn, "pause" if state == QMediaPlayer.PlayingState else "play", "text", 18)

    def toggle(self) -> None:
        if not self._path:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        player = self._ensure()
        if player.playbackState() == QMediaPlayer.PlayingState:
            player.pause()
        else:
            player.play()

    def play_from(self, seconds: float) -> None:
        if self._path:
            player = self._ensure()
            player.setPosition(int(seconds * 1000))
            player.play()

    def stop(self) -> None:
        if self._player:
            self._player.stop()


# ---------------------------------------------------------------- 액션 아이템


class ActionRow(QWidget):
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, item: ActionItem, parent=None):
        super().__init__(parent)
        self.done = QCheckBox()
        self.done.setChecked(item.done)
        self.done.setToolTip("완료")
        self.task = QLineEdit(item.task)
        self.task.setProperty("flat", True)
        self.task.setPlaceholderText("할 일")
        self.owner = QLineEdit(item.owner)
        self.owner.setProperty("flat", True)
        self.owner.setProperty("tone", "muted")
        self.owner.setPlaceholderText("담당")
        self.owner.setFixedWidth(84)
        self.due = QLineEdit(item.due)
        self.due.setProperty("flat", True)
        self.due.setProperty("tone", "muted")
        self.due.setPlaceholderText("기한")
        self.due.setFixedWidth(96)
        self.mine = QPushButton("내 일")
        self.mine.setProperty("variant", "chip")
        self.mine.setCheckable(True)
        self.mine.setChecked(item.is_mine)
        self.mine.setCursor(Qt.PointingHandCursor)
        self.mine.setToolTip("내 일이면 업무일지의 ‘내 할 일’과 명일 계획에 반영돼요")
        delete = icon_button("trash", "삭제", lambda: self.remove_requested.emit(self), "text3", 16)
        self.done.toggled.connect(self._on_done)
        self.mine.toggled.connect(self.changed.emit)
        for w in (self.task, self.owner, self.due):
            w.textEdited.connect(self.changed.emit)
        self.setLayout(hbox(self.done, self.task, self.owner, self.due, self.mine, delete, spacing=4, margins=(2, 1, 0, 1)))
        self._style_done()

    def _on_done(self, _on: bool) -> None:
        self._style_done()
        self.changed.emit()

    def _style_done(self) -> None:
        self.task.setProperty("done", self.done.isChecked())
        repolish(self.task)

    def value(self, meeting_id: int) -> ActionItem:
        return ActionItem(
            meeting_id=meeting_id,
            owner=self.owner.text().strip(),
            task=self.task.text().strip(),
            due=self.due.text().strip(),
            is_mine=self.mine.isChecked(),
            done=self.done.isChecked(),
        )


# ---------------------------------------------------------------- 상세


class MeetingDetail(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLineEdit()
        self.title.setProperty("flat", True)
        self.title.setProperty("level", "title")
        self.title.setPlaceholderText("회의 제목")
        self.status_chip = Chip("", "neutral")
        self.when = make_label("", "muted")
        self.source_chip = Chip("", "neutral")
        self.engine_chip = Chip("", "neutral")

        self.btn_rewrite = button("회의록 다시 작성", "secondary", "refresh")
        self.btn_copy = icon_button("copy", "Markdown 으로 복사")
        self.btn_export = icon_button("download", "Markdown 파일로 내보내기")
        self.btn_more = icon_button("more", "더 보기")
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self)
        self.act_retranscribe = QAction("음성 다시 변환", self.menu)
        self.act_reveal = QAction("녹음 파일 위치 열기", self.menu)
        self.act_delete = QAction("회의 삭제", self.menu)
        self.menu.addActions([self.act_retranscribe, self.act_reveal])
        self.menu.addSeparator()
        self.menu.addAction(self.act_delete)
        self.btn_more.setMenu(self.menu)

        # 상태 배너
        self.banner = QFrame()
        self.banner.setObjectName("AccentCard")
        self.banner_badge_box = QHBoxLayout()
        self.banner_title = make_label("", "section")
        self.banner_text = make_label("", "muted", wrap=True)
        self.banner_progress = QProgressBar()
        self.banner_progress.setRange(0, 100)
        self.banner_action = button("다시 시도", "secondary")
        self.banner.setLayout(
            hbox(self.banner_badge_box, vbox(self.banner_title, self.banner_text, self.banner_progress, spacing=4), self.banner_action, spacing=14, margins=(16, 14, 16, 14))
        )
        self.banner_kind = ""

        self.tabs = SegmentedControl([("minutes", "회의록"), ("transcript", "전사문")])

        # 회의록
        self.summary = AutoTextEdit("회의 목적과 결론을 2~4문장으로", min_lines=1)
        self.attendees = QLineEdit()
        self.attendees.setProperty("flat", True)
        self.attendees.setProperty("tone", "muted")
        self.attendees.setPlaceholderText("참석자 (쉼표로 구분)")
        self.decisions = AutoTextEdit("합의된 사항을 한 줄에 하나씩", min_lines=1)
        self.discussion = AutoTextEdit("## 주제\n- 논의 내용", min_lines=2)
        self.open_questions = AutoTextEdit("결론이 나지 않은 사항을 한 줄에 하나씩", min_lines=1)
        self.action_rows: list[ActionRow] = []
        self.actions_box = QVBoxLayout()
        self.actions_box.setSpacing(0)
        self.btn_add_action = button("액션아이템 추가", "ghost", "plus", on_click=lambda: self.add_action(ActionItem(meeting_id=0, task=""), focus=True))

        summary_card = Card("요약")
        summary_card.body.addWidget(self.summary)
        attendees_icon = QToolButton()
        attendees_icon.setEnabled(False)
        bind_icon(attendees_icon, "users", "text3", 16)
        attendees_row = hbox(attendees_icon, self.attendees, spacing=2)
        attendees_row.setStretch(1, 1)
        summary_card.body.addLayout(attendees_row)
        decisions_card = Card("결정 사항")
        decisions_card.body.addWidget(self.decisions)
        self.actions_card = Card("액션 아이템")
        self.actions_card.body.addLayout(self.actions_box)
        self.actions_card.body.addLayout(hbox(self.btn_add_action, None))
        discussion_card = Card("논의 내용")
        discussion_card.body.addWidget(self.discussion)
        open_card = Card("미결 사항")
        open_card.body.addWidget(self.open_questions)
        minutes_body = QWidget()
        minutes_body.setLayout(vbox(summary_card, self.actions_card, decisions_card, discussion_card, open_card, None, spacing=12, margins=(0, 0, 6, 12)))
        self.minutes_page = scroll_area(minutes_body)

        # 전사문
        self.transcript = TranscriptView()
        self.player = PlayerBar()
        self.transcript.itemDoubleClicked.connect(lambda item: self.player.play_from(item.data(Qt.UserRole)))
        transcript_page = QWidget()
        transcript_page.setLayout(vbox(make_label("말풍선을 더블클릭하면 그 위치부터 들을 수 있어요.", "caption"), self.transcript, self.player, spacing=8))
        self.transcript_empty = EmptyState("wave", "전사문이 아직 없어요", "음성 변환이 끝나면 대화 내용이 여기에 표시돼요.")
        self.transcript_stack = QStackedWidget()
        self.transcript_stack.addWidget(transcript_page)
        self.transcript_stack.addWidget(self.transcript_empty)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.minutes_page)
        self.stack.addWidget(self.transcript_stack)
        self.tabs.changed.connect(lambda key: self.stack.setCurrentIndex(0 if key == "minutes" else 1))

        self.saved_label = make_label("", "caption")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(hbox(self.title, self.btn_rewrite, self.btn_copy, self.btn_export, self.btn_more, spacing=4))
        layout.addLayout(hbox(self.status_chip, self.when, self.source_chip, self.engine_chip, None, spacing=8))
        layout.addWidget(self.banner)
        layout.addLayout(hbox(self.tabs, None, self.saved_label))
        layout.addWidget(self.stack, 1)

        self.title.textEdited.connect(self.changed.emit)
        self.attendees.textEdited.connect(self.changed.emit)
        for w in (self.summary, self.decisions, self.discussion, self.open_questions):
            w.textChanged.connect(self.changed.emit)

    # ------------------------------------------------------------
    def add_action(self, item: ActionItem, focus: bool = False) -> ActionRow:
        row = ActionRow(item)
        row.changed.connect(self.changed.emit)
        row.remove_requested.connect(self.remove_action)
        self.action_rows.append(row)
        self.actions_box.addWidget(row)
        self.actions_card.set_caption(f"{len(self.action_rows)}건")
        if focus:
            row.task.setFocus()
            self.changed.emit()
        return row

    def remove_action(self, row: ActionRow) -> None:
        if row in self.action_rows:
            self.action_rows.remove(row)
            discard(row)
            self.actions_card.set_caption(f"{len(self.action_rows)}건" if self.action_rows else "")
            self.changed.emit()

    def clear_actions(self) -> None:
        for row in self.action_rows:
            discard(row)
        self.action_rows = []
        self.actions_card.set_caption("")

    def set_banner(self, kind: str, title: str = "", text: str = "", progress: int | None = None, action_text: str = "") -> None:
        self.banner_kind = kind
        if not kind:
            self.banner.hide()
            return
        self.banner.setObjectName({"error": "DangerCard", "info": "AccentCard"}.get(kind, "SubtleCard"))
        repolish(self.banner)
        while self.banner_badge_box.count():
            w = self.banner_badge_box.takeAt(0).widget()
            if w:
                discard(w)
        icon_name, role = {"error": ("alert", "danger"), "info": ("wave", "accent")}.get(kind, ("key", "warning"))
        self.banner_badge_box.addWidget(IconBadge(icon_name, role, 38))
        self.banner_title.setText(title)
        self.banner_text.setText(text)
        self.banner_text.setVisible(bool(text))
        self.banner_progress.setVisible(progress is not None)
        if progress is not None:
            self.banner_progress.setValue(progress)
        self.banner_action.setVisible(bool(action_text))
        self.banner_action.setText(action_text)
        self.banner.show()


class MeetingsView(QWidget):
    status_message = Signal(str, str)
    record_requested = Signal()
    open_settings = Signal()

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.current_id: int | None = None
        self._loading = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(AUTOSAVE_MS)
        self.save_timer.timeout.connect(self.save_minutes)

        self.header = PageHeader("회의록", "Windows 녹음기 앱으로 녹음한 파일은 자동으로 들어와요")
        self.btn_import = button("파일 가져오기", "secondary", "upload", on_click=self.import_file)
        self.btn_record = button("회의 녹음", "record", "record", on_click=self.record_requested.emit)
        self.header.add_action(self.btn_import)
        self.header.add_action(self.btn_record)

        # 목록
        self.search = QLineEdit()
        self.search.setPlaceholderText("회의 검색")
        self.search.addAction(themed_icon("search", "text3", 16), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setItemDelegate(MeetingListDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemSelectionChanged.connect(self._on_selection)
        on_theme_change(self.list, self.list.viewport().update)
        self.list_empty = make_label("아직 회의가 없어요.", "caption")
        left = QWidget()
        left.setFixedWidth(330)
        left.setLayout(vbox(self.search, self.list, self.list_empty, spacing=10))

        # 상세
        self.detail = MeetingDetail()
        self.detail.changed.connect(self._changed)
        self.detail.btn_rewrite.clicked.connect(lambda: self.reprocess(force_transcribe=False))
        self.detail.btn_copy.clicked.connect(self.copy_markdown)
        self.detail.btn_export.clicked.connect(self.export_markdown)
        self.detail.act_retranscribe.triggered.connect(lambda: self.reprocess(force_transcribe=True))
        self.detail.act_reveal.triggered.connect(self.reveal_audio)
        self.detail.act_delete.triggered.connect(self.delete_meeting)
        self.detail.banner_action.clicked.connect(self._banner_action)
        self.empty = EmptyState(
            "mic",
            "회의를 선택하거나 새로 녹음하세요",
            "Windows 녹음기 앱으로 녹음해 저장하면 자동으로 가져와요. Teams·Zoom 회의는 ‘회의 녹음’으로 상대방 목소리까지 함께 녹음할 수 있어요.",
            button("회의 녹음 시작", "primary", "record", on_click=self.record_requested.emit),
        )
        self.detail_card = Card(fill=True, padding=22)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self.empty)
        self.detail_stack.addWidget(self.detail)
        self.detail_card.body.addWidget(self.detail_stack)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(left)
        body.addWidget(self.detail_card, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)
        layout.addWidget(self.header)
        layout.addLayout(body, 1)

        self.refresh_record_button()
        self.refresh_list()
        self._show(None)
        todo_signals(services).changed.connect(self.sync_action_done)

    # ------------------------------------------------------------ 녹음 버튼
    def refresh_record_button(self) -> None:
        rec = self.services.recorder
        if rec.is_recording:
            self.btn_record.setText(f"녹음 중지  {format_hms(rec.elapsed())}")
            set_variant(self.btn_record, "recording", "stop")
        else:
            self.btn_record.setText("회의 녹음")
            set_variant(self.btn_record, "record", "record")

    # ------------------------------------------------------------ 목록
    def _view_data(self, m: Meeting) -> dict:
        return {
            "title": m.title or "제목 없음",
            "meta": f"{meeting_when(m)} · {format_duration(m.duration_sec)} · {SOURCE_LABELS.get(m.source, m.source)}",
            "chip": status_chip(m),
            "progress": m.progress if m.status == MeetingStatus.TRANSCRIBING else None,
        }

    def refresh_list(self) -> None:
        meetings = self.services.db.list_meetings()
        self.list.blockSignals(True)
        self.list.clear()
        for m in meetings:
            item = QListWidgetItem()
            item.setData(ROLE_ID, m.id)
            item.setData(ROLE_VIEW, self._view_data(m))
            self.list.addItem(item)
            if m.id == self.current_id:
                item.setSelected(True)
        self.list.blockSignals(False)
        self.list_empty.setVisible(not meetings)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            view = item.data(ROLE_VIEW) or {}
            item.setHidden(bool(text) and text not in f"{view.get('title', '')} {view.get('meta', '')}".lower())

    def _item_of(self, meeting_id: int) -> QListWidgetItem | None:
        for i in range(self.list.count()):
            if self.list.item(i).data(ROLE_ID) == meeting_id:
                return self.list.item(i)
        return None

    def on_meeting_changed(self, meeting_id: int) -> None:
        meeting = self.services.db.get_meeting(meeting_id)
        item = self._item_of(meeting_id)
        if meeting is None or item is None:
            self.refresh_list()
        else:
            item.setData(ROLE_VIEW, self._view_data(meeting))
        if meeting_id == self.current_id and meeting is not None:
            if meeting.status in (MeetingStatus.DONE, MeetingStatus.ERROR, MeetingStatus.TRANSCRIBED) and not self.save_timer.isActive():
                self._show(meeting)
            else:
                self._update_header(meeting)

    def select_meeting(self, meeting_id: int) -> None:
        if self._item_of(meeting_id) is None:
            self.refresh_list()
        item = self._item_of(meeting_id)
        if item:
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item)

    def _on_selection(self) -> None:
        items = self.list.selectedItems()
        new_id = items[0].data(ROLE_ID) if items else None
        if new_id == self.current_id:
            return
        self.flush()
        self.detail.player.stop()
        self.current_id = new_id
        self._show(self.services.db.get_meeting(new_id) if new_id else None)

    # ------------------------------------------------------------ 상세
    def _update_header(self, m: Meeting) -> None:
        d = self.detail
        label, kind = status_chip(m)
        d.status_chip.set_kind(kind, label)
        d.when.setText(f"{meeting_when(m)} · {format_duration(m.duration_sec)}")
        d.source_chip.setText(SOURCE_LABELS.get(m.source, m.source))
        d.engine_chip.setText(m.stt_engine)
        d.engine_chip.setVisible(bool(m.stt_engine))
        if m.status in (MeetingStatus.QUEUED, MeetingStatus.TRANSCRIBING):
            d.set_banner("info", "음성을 텍스트로 변환하고 있어요", "녹음이 길면 몇 분 걸릴 수 있어요. 끝나면 알림으로 알려 드릴게요.", int(m.progress * 100))
        elif m.status == MeetingStatus.SUMMARIZING:
            d.set_banner("info", "Claude 가 회의록을 정리하고 있어요", "요약·결정 사항·액션아이템을 뽑는 중이에요.")
        elif m.status == MeetingStatus.ERROR:
            d.set_banner("error", "처리하지 못했어요", m.error, None, "다시 시도")
        elif m.status == MeetingStatus.TRANSCRIBED and not self.services.claude.available():
            d.set_banner("warning", "회의록 자동 정리가 꺼져 있어요", "설정에서 Claude API 키를 입력하면 요약·결정 사항·액션아이템을 자동으로 정리해요.", None, "설정 열기")
        else:
            d.set_banner("")
        d.btn_rewrite.setEnabled(m.status not in MeetingStatus.PENDING)
        has_audio = bool(m.audio_path and Path(m.audio_path).exists())
        d.act_retranscribe.setEnabled(has_audio and m.status not in MeetingStatus.PENDING)
        d.act_reveal.setEnabled(has_audio)

    def _show(self, m: Meeting | None) -> None:
        self._loading = True
        d = self.detail
        if m is None:
            self.detail_stack.setCurrentWidget(self.empty)
            self._loading = False
            return
        self.detail_stack.setCurrentWidget(d)
        self._update_header(m)
        minutes = m.minutes
        d.title.setText(m.title)
        d.attendees.setText(", ".join(minutes.attendees) if minutes else "")
        d.summary.setPlainText(minutes.summary if minutes else "")
        d.discussion.setPlainText(discussion_to_text(minutes.discussion) if minutes else "")
        d.decisions.setPlainText("\n".join(minutes.decisions) if minutes else "")
        d.open_questions.setPlainText("\n".join(minutes.open_questions) if minutes else "")
        d.clear_actions()
        for item in self.services.db.action_items_for(m.id):
            d.add_action(item)
        d.transcript.clear()
        segments = m.transcript.segments if m.transcript else []
        for seg in segments:
            li = QListWidgetItem()
            li.setData(Qt.UserRole, seg.start)
            li.setData(Qt.UserRole + 1, seg.speaker)
            li.setData(Qt.UserRole + 2, seg.text.strip())
            d.transcript.addItem(li)
        d.transcript_stack.setCurrentIndex(0 if segments else 1)
        d.tabs.set_label("transcript", f"전사문 {len(segments)}" if segments else "전사문")
        d.player.set_source(m.audio_path)
        d.saved_label.setText("")
        self._loading = False

    def sync_action_done(self) -> None:
        """오늘 화면에서 체크한 액션아이템의 완료 상태를 열려 있는 회의록에도 맞춘다."""
        if self.current_id is None:
            return
        done = {a.task: a.done for a in self.services.db.action_items_for(self.current_id)}
        for row in self.detail.action_rows:
            task = row.task.text().strip()
            if task in done and row.done.isChecked() != done[task]:
                row.done.blockSignals(True)
                row.done.setChecked(done[task])
                row.done.blockSignals(False)
                row._style_done()

    def _changed(self) -> None:
        if self._loading or self.current_id is None:
            return
        self.detail.saved_label.setText("저장 중…")
        self.save_timer.start()

    def flush(self) -> None:
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.save_minutes()

    def save_minutes(self) -> None:
        self.save_timer.stop()
        if self.current_id is None:
            return
        meeting = self.services.db.get_meeting(self.current_id)
        if meeting is None:
            return
        d = self.detail
        actions = [a for a in (row.value(self.current_id) for row in d.action_rows) if a.task]
        minutes = MeetingMinutes(
            title=d.title.text().strip(),
            summary=d.summary.toPlainText().strip(),
            attendees=[a.strip() for a in d.attendees.text().split(",") if a.strip()],
            discussion=text_to_discussion(d.discussion.toPlainText()),
            decisions=_lines(d.decisions.toPlainText()),
            action_items=[{"owner": a.owner, "task": a.task, "due": a.due, "is_mine": a.is_mine} for a in actions],
            open_questions=_lines(d.open_questions.toPlainText()),
        )
        fields: dict = {"minutes": minutes}
        if minutes.title:
            fields["title"] = minutes.title
        if meeting.status in (MeetingStatus.TRANSCRIBED, MeetingStatus.ERROR) and meeting.transcript and (minutes.summary or minutes.decisions or actions):
            fields["status"] = MeetingStatus.DONE
            fields["error"] = ""
        self.services.db.update_meeting(self.current_id, **fields)
        self.services.db.replace_action_items(self.current_id, actions)
        self.services.todos.notify()  # 오늘 할 일 목록도 새로 고친다
        updated = self.services.db.get_meeting(self.current_id)
        item = self._item_of(self.current_id)
        if item and updated:
            item.setData(ROLE_VIEW, self._view_data(updated))
            self._update_header(updated)
        d.saved_label.setText("자동 저장됨")

    # ------------------------------------------------------------ 작업
    def _banner_action(self) -> None:
        meeting = self.services.db.get_meeting(self.current_id) if self.current_id else None
        if meeting and meeting.status == MeetingStatus.TRANSCRIBED and not self.services.claude.available():
            self.open_settings.emit()
        else:
            self.reprocess(force_transcribe=False)

    def import_file(self) -> None:
        start_dir = str(self.services.store.get().recorder_path())
        path, _ = QFileDialog.getOpenFileName(self, "회의 녹음 파일 가져오기", start_dir, AUDIO_FILTER)
        if not path:
            return

        def done(m: Meeting) -> None:
            self.refresh_list()
            self.select_meeting(m.id)
            self.status_message.emit(f"‘{m.title}’ 음성 변환을 시작했어요.", "success")

        run_async(self.services.import_audio, Path(path), on_done=done, on_error=lambda e: self.status_message.emit(f"가져오지 못했어요: {e}", "error"))

    def reprocess(self, force_transcribe: bool) -> None:
        if self.current_id is None:
            return
        self.flush()
        meeting = self.services.db.get_meeting(self.current_id)
        has_audio = bool(meeting and meeting.audio_path and Path(meeting.audio_path).exists())
        if force_transcribe and not has_audio:
            self.status_message.emit("녹음 파일이 없어 다시 변환할 수 없어요.", "error")
            return
        if not force_transcribe and not (meeting and meeting.transcript):
            force_transcribe = True
        if not force_transcribe and not self.services.claude.available():
            self.status_message.emit("설정에서 Claude API 키를 입력하면 회의록을 작성할 수 있어요.", "error")
            return
        if meeting.minutes and QMessageBox.question(self, "다시 처리", "지금 회의록을 새로 작성한 내용으로 바꿀까요?") != QMessageBox.Yes:
            return
        self.services.db.update_meeting(self.current_id, status=MeetingStatus.QUEUED, progress=0.0, error="")
        self.services.pipeline.enqueue(self.current_id, force_transcribe=force_transcribe)
        self.on_meeting_changed(self.current_id)

    def delete_meeting(self) -> None:
        if self.current_id is None:
            return
        meeting = self.services.db.get_meeting(self.current_id)
        if QMessageBox.question(self, "회의 삭제", f"‘{meeting.title}’ 회의와 회의록을 삭제할까요?\n(녹음기 앱 폴더의 원본 파일은 지우지 않아요)") != QMessageBox.Yes:
            return
        self.save_timer.stop()
        self.detail.player.stop()
        own = paths.audio_dir().resolve()
        audio = Path(meeting.audio_path) if meeting.audio_path else None
        if audio and audio.exists() and own in audio.resolve().parents:
            audio.unlink(missing_ok=True)
        self.services.db.delete_meeting(self.current_id)
        self.current_id = None
        self.services.todos.notify()
        self.refresh_list()
        self._show(None)
        self.status_message.emit("회의를 삭제했어요.", "info")

    def reveal_audio(self) -> None:
        meeting = self.services.db.get_meeting(self.current_id) if self.current_id else None
        if not meeting or not meeting.audio_path:
            return
        path = Path(meeting.audio_path)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif hasattr(os, "startfile"):
            os.startfile(path.parent)  # type: ignore[attr-defined]

    def current_markdown(self) -> str:
        self.flush()
        meeting = self.services.db.get_meeting(self.current_id)
        return minutes_to_markdown(meeting, self.services.db.action_items_for(meeting.id))

    def copy_markdown(self) -> None:
        if self.current_id is not None:
            QApplication.clipboard().setText(self.current_markdown())
            self.status_message.emit("회의록을 클립보드에 복사했어요.", "success")

    def export_markdown(self) -> None:
        if self.current_id is None:
            return
        meeting = self.services.db.get_meeting(self.current_id)
        safe = "".join(ch for ch in meeting.title if ch not in '\\/:*?"<>|').strip() or "회의록"
        default = str(paths.documents_dir() / f"회의록_{meeting.date}_{safe}.md")
        path, _ = QFileDialog.getSaveFileName(self, "회의록 내보내기", default, "Markdown (*.md)")
        if path:
            Path(path).write_text(self.current_markdown(), encoding="utf-8")
            self.status_message.emit(f"저장했어요: {path}", "success")
