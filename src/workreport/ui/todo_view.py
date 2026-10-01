"""오늘 할 일 패널: 직접 추가한 할 일 + 회의 액션아이템(내 일) + 어제 '명일 계획'.

- 전체 모드(오늘 화면): 빠른 추가(기한·중요), 진행률, 항목 편집, 완료 접기.
- 간단 모드(업무일지 화면): 남은 할 일만, 항목마다 '명일 계획에 추가'.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Callable

from PySide6.QtCore import QDate, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QDialog,
    QFrame,
    QLineEdit,
    QMenu,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..report.render import WEEKDAYS
from ..services import Services
from ..todos import KIND_ACTION, KIND_PLAN, TodoEntry
from .icons import bind_icon, render_pixmap, themed_icon
from .theme import on_theme_change, repolish, tokens
from .widgets import Card, Chip, ElidedLabel, IconBadge, button, clear_layout, hbox, icon_button, make_label, vbox

MAX_OPEN = 6  # 오늘 화면에서 처음에 보이는 남은 할 일 수
MAX_COMPACT = 8
DONE_HOLD_SEC = 0.35  # 체크한 항목이 완료 쪽으로 옮겨가기 전 잠깐 보여 주는 시간
SOURCE_CHIP_CHARS = 16
DUE_SLOT_WIDTH = 92


# ---------------------------------------------------------------- 신호 다리


class _TodoSignals(QObject):
    changed = Signal()


def todo_signals(services: Services) -> _TodoSignals:
    """TodoService 변경 → Qt 신호. 다른 스레드에서 바뀌어도 UI 스레드에서 받는다."""
    bridge = getattr(services, "_todo_signals", None)
    if bridge is None:
        bridge = _TodoSignals(QApplication.instance())
        services.todos.listeners.append(bridge.changed.emit)
        services._todo_signals = bridge
    return bridge


# ---------------------------------------------------------------- 기한


def short_date(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.month}/{d.day}"


def due_text(entry: TodoEntry, today: str) -> tuple[str, str]:
    """(문구, 칩 종류). 기한이 없으면 ('', '')."""
    state = entry.due_state(today)
    if state == "none":
        return "", ""
    if state == "overdue":
        return f"{short_date(entry.due)} 지남", "danger"
    if state == "today":
        return "오늘까지", "warning"
    if state == "soon":
        return "내일까지", "accent"
    return f"{short_date(entry.due)}까지", "neutral"


def due_options(today: date) -> list[tuple[str, str]]:
    """기한 메뉴의 빠른 선택지: (라벨, YYYY-MM-DD)."""
    options = [("오늘", today), ("내일", today + timedelta(days=1))]
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    if friday > today + timedelta(days=1):
        options.append(("이번 주 금요일", friday))
    options.append(("다음 주 월요일", today + timedelta(days=7 - today.weekday())))
    return [(f"{label}  ·  {d.month}/{d.day} ({WEEKDAYS[d.weekday()]})", d.isoformat()) for label, d in options]


def due_label(due: str, today: date) -> str:
    """빠른 추가 줄의 기한 버튼 문구."""
    if not due:
        return "기한"
    d = date.fromisoformat(due)
    if d == today:
        return "오늘"
    if d == today + timedelta(days=1):
        return "내일"
    return f"{d.month}/{d.day} ({WEEKDAYS[d.weekday()]})"


class DatePickerDialog(QDialog):
    def __init__(self, current: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("기한 선택")
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setMinimumDate(QDate.currentDate().addYears(-1))
        if current:
            self.calendar.setSelectedDate(QDate.fromString(current, "yyyy-MM-dd"))
        self.calendar.activated.connect(lambda _d: self.accept())
        self.calendar.clicked.connect(lambda _d: self.accept())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.calendar)

    def value(self) -> str:
        return self.calendar.selectedDate().toString("yyyy-MM-dd")


def show_due_menu(anchor: QWidget, current: str, on_pick: Callable[[str], None]) -> None:
    menu = QMenu(anchor)
    for label, value in due_options(date.today()):
        menu.addAction(label).triggered.connect(lambda _c=False, v=value: on_pick(v))
    menu.addSeparator()

    def pick_date() -> None:
        dialog = DatePickerDialog(current, anchor.window())
        if dialog.exec():
            on_pick(dialog.value())

    menu.addAction(themed_icon("calendar", "text2", 16), "날짜 선택…").triggered.connect(pick_date)
    if current:
        menu.addAction("기한 없애기").triggered.connect(lambda: on_pick(""))
    menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))


# ---------------------------------------------------------------- 공통 조각


def star_button(checked: bool = False, tooltip: str = "중요") -> QToolButton:
    """빈 별(꺼짐) / 채운 별(켜짐) 토글."""
    btn = QToolButton()
    btn.setCheckable(True)
    btn.setChecked(checked)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tooltip)

    def apply() -> None:
        t = tokens()
        ic = QIcon()
        ic.addPixmap(render_pixmap("star", t.text3, 16), QIcon.Normal, QIcon.Off)
        ic.addPixmap(render_pixmap("star-filled", t.warning, 16), QIcon.Normal, QIcon.On)
        ic.addPixmap(render_pixmap("star", t.text2, 16), QIcon.Active, QIcon.Off)
        ic.addPixmap(render_pixmap("star-filled", t.warning, 16), QIcon.Active, QIcon.On)
        btn.setIcon(ic)
        btn.setIconSize(QSize(16, 16))

    apply()
    on_theme_change(btn, apply)
    return btn


def source_chip(entry: TodoEntry) -> Chip | None:
    if not entry.subtitle:
        return None
    text = entry.subtitle
    if len(text) > SOURCE_CHIP_CHARS:
        text = text[: SOURCE_CHIP_CHARS - 1] + "…"
    chip = Chip(text, "meeting" if entry.kind == KIND_ACTION else "accent")
    chip.setToolTip(entry.subtitle + ("\n회의록 화면에서 내용을 고칠 수 있어요." if entry.kind == KIND_ACTION else ""))
    return chip


class ClickableChip(Chip):
    clicked = Signal()

    def __init__(self, text: str, kind: str, clickable: bool, parent=None):
        super().__init__(text, kind, parent)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("기한 바꾸기")
        self._clickable = clickable

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit()
            return
        super().mousePressEvent(event)


# ---------------------------------------------------------------- 항목 행


def _retain_size(widget: QWidget) -> None:
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


class TodoRow(QFrame):
    """체크 · 제목(바로 편집) · 출처 칩 · 기한 칩 · 중요 · 삭제."""

    toggled = Signal(object, bool)
    renamed = Signal(object, str)
    due_requested = Signal(object, QWidget)
    important_toggled = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, entry: TodoEntry, today: str, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("TodoRow")

        self.check = QCheckBox()
        self.check.setChecked(entry.done)
        self.check.setCursor(Qt.PointingHandCursor)
        self.check.setToolTip("완료 취소" if entry.done else "완료")
        self.check.toggled.connect(self._on_toggled)

        self.title = QLineEdit(entry.title)
        self.title.setProperty("flat", True)
        self.title.setCursorPosition(0)
        if entry.editable:
            self.title.setToolTip("눌러서 바로 고칠 수 있어요")
            self.title.editingFinished.connect(self._on_edited)
        else:
            self.title.setReadOnly(True)
            self.title.setFocusPolicy(Qt.NoFocus)
            self.title.setCursor(Qt.ArrowCursor)
        self._style_done(entry.done)

        items: list = [self.check, self.title]
        self.source = source_chip(entry)
        if self.source:
            items.append(self.source)

        # 오른쪽 열(기한 · 중요 · 삭제)은 모든 행에서 같은 폭을 차지해 칩이 세로로 맞는다
        text, kind = due_text(entry, today)
        self.due_chip: ClickableChip | None = None
        self.btn_due: QToolButton | None = None
        self.hover_buttons: list[QWidget] = []
        due_slot = QWidget()
        due_slot.setFixedWidth(DUE_SLOT_WIDTH)
        if text and not entry.done:
            self.due_chip = ClickableChip(text, kind, entry.editable)
            self.due_chip.setToolTip(f"기한 {entry.due}" + ("\n눌러서 바꾸기" if entry.editable else ""))
            self.due_chip.clicked.connect(lambda: self.due_requested.emit(self.entry, self.due_chip))
            due_slot.setLayout(hbox(None, self.due_chip))
        elif entry.editable and not entry.done:
            self.btn_due = icon_button("calendar", "기한 정하기", lambda: self.due_requested.emit(self.entry, self.btn_due), "text3", 16)
            self.hover_buttons.append(self.btn_due)
            due_slot.setLayout(hbox(None, self.btn_due))
        items.append(due_slot)

        self.star = star_button(entry.important, "중요 표시")
        self.btn_delete = icon_button("trash", "삭제", lambda: self.delete_requested.emit(self.entry), "text3", 16)
        if entry.editable:
            self.star.clicked.connect(lambda: self.important_toggled.emit(self.entry))
            if not entry.important:
                self.hover_buttons.append(self.star)
            self.hover_buttons.append(self.btn_delete)
        else:  # 액션아이템: 자리만 차지한다(회의록에서 관리)
            self.star.setEnabled(False)
            for widget in (self.star, self.btn_delete):
                _retain_size(widget)
                widget.setVisible(False)
        items += [self.star, self.btn_delete]

        for widget in self.hover_buttons:
            _retain_size(widget)
            widget.setVisible(False)

        layout = hbox(*items, spacing=6, margins=(8, 2, 6, 2))
        layout.setStretch(1, 1)
        self.setLayout(layout)
        self.setMinimumHeight(38)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        for widget in self.hover_buttons:
            widget.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        for widget in self.hover_buttons:
            widget.setVisible(False)
        super().leaveEvent(event)

    def _style_done(self, done: bool) -> None:
        self.title.setProperty("done", done)
        self.title.setProperty("tone", "muted" if done else None)
        repolish(self.title)

    def _on_toggled(self, on: bool) -> None:
        self._style_done(on)
        self.toggled.emit(self.entry, on)

    def _on_edited(self) -> None:
        text = self.title.text().strip()
        if not text:
            self.title.setText(self.entry.title)
            return
        if text != self.entry.title:
            self.renamed.emit(self.entry, text)

    def is_editing(self) -> bool:
        return self.title.hasFocus() and self.title.isModified()


class CompactTodoRow(QWidget):
    """업무일지 화면용: 체크 · 제목/출처 · 명일 계획에 추가."""

    toggled = Signal(object, bool)
    plan_requested = Signal(object)

    def __init__(self, entry: TodoEntry, today: str, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.check = QCheckBox()
        self.check.setToolTip("완료")
        self.check.setCursor(Qt.PointingHandCursor)
        self.check.toggled.connect(lambda on: self.toggled.emit(self.entry, on))
        self.title = ElidedLabel(("★ " if entry.important else "") + entry.title)
        parts = [entry.subtitle or "직접 추가"]
        text, _kind = due_text(entry, today)
        if text:
            parts.append(text)
        caption = make_label(" · ".join(parts), "caption")
        if entry.due_state(today) == "overdue":
            caption.setProperty("tone", "danger")
        self.btn_plan = icon_button("plus", "명일 계획에 추가", lambda: self.plan_requested.emit(self.entry), "text2", 16)
        layout = hbox(self.check, vbox(self.title, caption, spacing=0), self.btn_plan, spacing=8, margins=(0, 3, 0, 3))
        layout.setStretch(1, 1)
        self.setLayout(layout)


# ---------------------------------------------------------------- 패널


class TodoPanel(Card):
    changed = Signal()
    status_message = Signal(str, str)
    plan_requested = Signal(object)  # 간단 모드: TodoEntry → 명일 계획

    def __init__(self, services: Services, compact: bool = False, parent=None):
        super().__init__("남은 할 일" if compact else "오늘 할 일", parent=parent)
        self.services = services
        self.compact = compact
        self.show_all = False
        self.show_done = False
        self.new_due = ""
        self.rows: list[TodoRow | CompactTodoRow] = []
        self.done_rows: list[TodoRow] = []
        self._signature: tuple | None = None
        self._hold_until = 0.0

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)

        if not compact:
            self._build_header()
            self._build_add_row()
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(2)
        self.body.addLayout(self.list_box)
        self.more_btn = button("", "link", on_click=self._toggle_all)
        self.more_btn.setVisible(False)
        self.body.addLayout(hbox(self.more_btn, None, margins=(8, 0, 0, 0)))
        if not compact:
            self.done_toggle = button("", "ghost", "chevron-down", on_click=self._toggle_done)
            self.done_toggle.setVisible(False)
            self.done_box = QVBoxLayout()
            self.done_box.setSpacing(2)
            self.body.addLayout(hbox(self.done_toggle, None))
            self.body.addLayout(self.done_box)

        todo_signals(services).changed.connect(self._on_service_changed)
        self.refresh()

    # ------------------------------------------------------------ 구성
    def _build_header(self) -> None:
        self.progress_label = make_label("", "caption")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(120)
        self.progress.setTextVisible(False)
        self.add_action(self.progress_label)
        self.add_action(self.progress)
        self.actions.setSpacing(10)

    def _build_add_row(self) -> None:
        self.add_frame = QFrame()
        self.add_frame.setObjectName("TodoAdd")
        plus = QToolButton()
        plus.setEnabled(False)
        bind_icon(plus, "plus", "text3", 16)
        self.input = QLineEdit()
        self.input.setPlaceholderText("할 일을 입력하고 Enter  (예: 릴리스 노트 초안 공유)")
        self.input.returnPressed.connect(self.add_from_input)
        self.btn_new_due = QToolButton()
        self.btn_new_due.setProperty("variant", "due")
        self.btn_new_due.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_new_due.setCursor(Qt.PointingHandCursor)
        self.btn_new_due.setToolTip("기한 정하기")
        bind_icon(self.btn_new_due, "calendar", "text2", 15)
        self.btn_new_due.clicked.connect(lambda: show_due_menu(self.btn_new_due, self.new_due, self.set_new_due))
        self.btn_new_important = star_button(False, "중요 표시")
        self.btn_add = button("추가", "primary", on_click=self.add_from_input)
        self.btn_add.setEnabled(False)
        self.input.textChanged.connect(lambda text: self.btn_add.setEnabled(bool(text.strip())))
        self.add_frame.setLayout(hbox(plus, self.input, self.btn_new_due, self.btn_new_important, self.btn_add, spacing=4, margins=(6, 4, 4, 4)))
        self.set_new_due("")
        self.body.addWidget(self.add_frame)

    # ------------------------------------------------------------ 갱신
    def _on_service_changed(self) -> None:
        delay = max(0.0, self._hold_until - time.monotonic())
        self._timer.start(int(delay * 1000))

    def _force_refresh(self) -> None:
        self._signature = None
        self.refresh()

    def _editing(self) -> bool:
        return any(isinstance(r, TodoRow) and r.is_editing() for r in self.rows)

    def refresh(self) -> None:
        if self._editing():  # 제목을 고치는 중이면 다시 그리지 않는다
            self._timer.start(800)
            return
        today = date.today().isoformat()
        todos = self.services.todos
        open_entries = todos.open_entries(today)
        done_entries = [] if self.compact else todos.completed_on(today)[::-1]
        signature = (
            today,
            self.show_all,
            self.show_done,
            tuple((e.key, e.title, e.subtitle, e.due, e.important) for e in open_entries),
            tuple((e.key, e.title) for e in done_entries),
        )
        if signature == self._signature:
            return
        self._signature = signature

        clear_layout(self.list_box)
        self.rows = []
        limit = MAX_COMPACT if self.compact else MAX_OPEN
        visible = open_entries if self.show_all else open_entries[:limit]
        for entry in visible:
            row = self._make_row(entry, today)
            self.rows.append(row)
            self.list_box.addWidget(row)
        hidden = len(open_entries) - len(visible)
        self.more_btn.setVisible(hidden > 0 or (self.show_all and len(open_entries) > limit))
        self.more_btn.setText(f"남은 할 일 {hidden}개 더 보기" if hidden > 0 else "접기")

        if not open_entries:
            self.list_box.addWidget(self._empty(bool(done_entries)))

        total = len(open_entries) + len(done_entries)
        if self.compact:
            self.set_caption(f"{len(open_entries)}개" if open_entries else "")
        else:
            self.set_caption(f"남은 {len(open_entries)}개" if open_entries else "")
            self.progress_label.setText(f"{len(done_entries)} / {total} 완료" if total else "")
            self.progress.setVisible(bool(total))
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(len(done_entries))
            self._fill_done(done_entries, today)
        self.changed.emit()

    def _make_row(self, entry: TodoEntry, today: str):
        if self.compact:
            row = CompactTodoRow(entry, today)
            row.toggled.connect(self._toggle)
            row.plan_requested.connect(self.plan_requested.emit)
            return row
        row = TodoRow(entry, today)
        row.toggled.connect(self._toggle)
        row.renamed.connect(self._rename)
        row.due_requested.connect(self._ask_due)
        row.important_toggled.connect(self._toggle_important)
        row.delete_requested.connect(self._delete)
        return row

    def _fill_done(self, done_entries: list[TodoEntry], today: str) -> None:
        clear_layout(self.done_box)
        self.done_rows = []
        self.done_toggle.setVisible(bool(done_entries))
        self.done_toggle.setText(f"완료한 일 {len(done_entries)}개")
        bind_icon(self.done_toggle, "chevron-up" if self.show_done else "chevron-down", "text2", 16)
        if not self.show_done:
            return
        for entry in done_entries:
            row = self._make_row(entry, today)
            self.done_rows.append(row)
            self.done_box.addWidget(row)

    def _empty(self, all_done: bool) -> QWidget:
        if self.compact:
            return make_label("남은 할 일이 없어요.", "caption")
        if all_done:
            title, text = "오늘 할 일을 모두 끝냈어요", "끝낸 일은 오늘 업무일지의 금일 실적에 반영돼요."
        else:
            title, text = "아직 할 일이 없어요", "위에 바로 적어 보세요. 회의에서 맡은 일과 어제 업무일지의 명일 계획도 여기에 자동으로 모여요."
        box = QWidget()
        box.setLayout(
            hbox(IconBadge("check-circle" if all_done else "list", "success" if all_done else "accent", 34), vbox(make_label(title, None), make_label(text, "caption", wrap=True), spacing=2), spacing=12, margins=(8, 6, 8, 6))
        )
        box.layout().setStretch(1, 1)
        return box

    # ------------------------------------------------------------ 빠른 추가
    def set_new_due(self, due: str) -> None:
        self.new_due = due
        self.btn_new_due.setText(due_label(due, date.today()))
        self.btn_new_due.setProperty("set", bool(due))
        repolish(self.btn_new_due)

    def add_from_input(self) -> None:
        title = self.input.text().strip()
        if not title:
            return
        self.services.todos.add(title, due=self.new_due, important=self.btn_new_important.isChecked())
        self.input.clear()
        self.set_new_due("")
        self.btn_new_important.setChecked(False)
        self.refresh()

    # ------------------------------------------------------------ 항목 동작
    def _toggle(self, entry: TodoEntry, done: bool) -> None:
        self._hold_until = time.monotonic() + DONE_HOLD_SEC  # 체크 표시를 잠깐 보여 준 뒤 옮긴다
        self.services.todos.set_done(entry, done)
        if done and entry.kind == KIND_ACTION:
            self.status_message.emit("회의록의 액션아이템도 완료로 표시했어요.", "success")

    def _rename(self, entry: TodoEntry, title: str) -> None:
        self._guard(lambda: self.services.todos.rename(entry, title))

    def _ask_due(self, entry: TodoEntry, anchor: QWidget) -> None:
        show_due_menu(anchor, entry.due, lambda due: self._guard(lambda: self.services.todos.set_due(entry, due)))

    def _toggle_important(self, entry: TodoEntry) -> None:
        self._guard(lambda: self.services.todos.toggle_important(entry))

    def _delete(self, entry: TodoEntry) -> None:
        if self._guard(lambda: self.services.todos.delete(entry)):
            note = " 같은 계획이 다시 들어오지 않아요." if entry.kind == KIND_PLAN else ""
            self.status_message.emit(f"‘{entry.title}’ 할 일을 지웠어요.{note}", "info")

    def _guard(self, fn: Callable[[], None]) -> bool:
        try:
            fn()
            return True
        except ValueError as exc:
            self.status_message.emit(str(exc), "error")
            self._force_refresh()
            return False

    def _toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.refresh()

    def _toggle_done(self) -> None:
        self.show_done = not self.show_done
        self.refresh()

    # ------------------------------------------------------------ 조회(테스트·다른 화면)
    def open_titles(self) -> list[str]:
        return [r.entry.title for r in self.rows]

    def done_titles(self) -> list[str]:
        return [r.entry.title for r in self.done_rows]
