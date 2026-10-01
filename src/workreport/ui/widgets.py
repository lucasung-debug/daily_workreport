"""공통 UI 컴포넌트: 카드, 칩, 버튼, 토글, 세그먼트, 자동 높이 입력, 빈 상태, 토스트, 타임라인, 사용량 막대."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Callable, Iterable

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .icons import bind_icon, render_pixmap
from .theme import font, on_theme_change, qcolor, repolish, tokens

# ---------------------------------------------------------------- 기본 요소


def discard(widget: QWidget) -> None:
    """위젯을 즉시 화면에서 떼어 내고 나중에 삭제한다(deleteLater 만으로는 잠시 남아 겹쳐 보일 수 있다)."""
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            discard(item.widget())
        elif item.layout():
            clear_layout(item.layout())


def make_label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


class Chip(QLabel):
    def __init__(self, text: str = "", kind: str = "neutral", parent=None):
        super().__init__(text, parent)
        self.setProperty("chip", kind)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set_kind(self, kind: str, text: str | None = None) -> None:
        if text is not None:
            self.setText(text)
        if self.property("chip") != kind:
            self.setProperty("chip", kind)
            repolish(self)


_ICON_ROLE_FOR_VARIANT = {"primary": "on_accent", "danger": "on_accent", "recording": "on_accent", "record": "danger", "ghost": "text2", "link": "accent_text"}


def button(text: str, variant: str = "secondary", icon_name: str | None = None, on_click: Callable | None = None, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    if icon_name:
        bind_icon(btn, icon_name, _ICON_ROLE_FOR_VARIANT.get(variant, "text2"), 16)
    if on_click:
        btn.clicked.connect(lambda *_: on_click())
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def set_variant(btn: QPushButton, variant: str, icon_name: str | None = None) -> None:
    """버튼 스타일을 바꾼다. 바뀐 경우에만 다시 칠한다(1초마다 불려도 비용이 없다)."""
    if btn.property("variant") == variant and btn.property("icon_name") == icon_name:
        return
    btn.setProperty("variant", variant)
    repolish(btn)
    if icon_name:
        btn.setProperty("icon_name", icon_name)
        bind_icon(btn, icon_name, _ICON_ROLE_FOR_VARIANT.get(variant, "text2"), 16)


def icon_button(name: str, tooltip: str = "", on_click: Callable | None = None, role: str = "text2", size: int = 18) -> QToolButton:
    btn = QToolButton()
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tooltip)
    btn.setAutoRaise(True)
    bind_icon(btn, name, role, size)
    if on_click:
        btn.clicked.connect(lambda *_: on_click())
    return btn


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


def hbox(*items, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for item in items:
        if item is None:
            layout.addStretch(1)
        elif isinstance(item, int):
            layout.addSpacing(item)
        elif isinstance(item, QWidget):
            layout.addWidget(item)
        else:
            layout.addLayout(item)
    return layout


def vbox(*items, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for item in items:
        if item is None:
            layout.addStretch(1)
        elif isinstance(item, int):
            layout.addSpacing(item)
        elif isinstance(item, QWidget):
            layout.addWidget(item)
        else:
            layout.addLayout(item)
    return layout


def scroll_area(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setObjectName("Transparent")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    widget.setObjectName(widget.objectName() or "ScrollBody")
    widget.setAttribute(Qt.WA_StyledBackground, False)
    area.setWidget(widget)
    area.viewport().setAutoFillBackground(False)
    widget.setAutoFillBackground(False)
    return area


class Card(QFrame):
    """제목·보조 설명·오른쪽 동작 버튼을 가진 카드."""

    def __init__(self, title: str = "", caption: str = "", object_name: str = "Card", padding: int = 18, fill: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding - 2, padding, padding)
        outer.setSpacing(12)
        self.title_label = make_label(title, "section")
        self.caption_label = make_label(caption, "caption")
        self.actions = QHBoxLayout()
        self.actions.setSpacing(4)
        if title or caption:
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(self.title_label)
            head.addWidget(self.caption_label)
            head.addStretch(1)
            head.addLayout(self.actions)
            outer.addLayout(head)
            self.caption_label.setVisible(bool(caption))
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1 if fill else 0)
        if not fill:
            outer.addStretch(1)  # 카드가 늘어나도 내용은 위에 붙인다

    def set_caption(self, text: str) -> None:
        self.caption_label.setText(text)
        self.caption_label.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.title = make_label(title, "title")
        self.subtitle = make_label(subtitle, "subtitle")
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        left = vbox(self.title, self.subtitle, spacing=2)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(left)
        layout.addStretch(1)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class IconBadge(QWidget):
    """연한 배경 원 안의 아이콘."""

    def __init__(self, name: str, role: str = "accent", size: int = 36, parent=None):
        super().__init__(parent)
        self.name, self.role, self.size_ = name, role, size
        self.setFixedSize(size, size)
        on_theme_change(self, self.update)

    def paintEvent(self, _event) -> None:
        t = tokens()
        color = getattr(t, self.role)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(color, 40 if t.dark else 26))
        p.drawRoundedRect(QRectF(0, 0, self.size_, self.size_), self.size_ * 0.3, self.size_ * 0.3)
        icon_size = int(self.size_ * 0.5)
        pm = render_pixmap(self.name, color, icon_size)
        off = (self.size_ - icon_size) / 2
        p.drawPixmap(QPoint(int(off), int(off)), pm)
        p.end()


class StatCard(QFrame):
    def __init__(self, label: str, icon_name: str, role: str = "accent", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.label = make_label(label, "statlabel")
        self.value = make_label("—", "stat")
        self.sub = make_label("", "caption")
        self.badge = IconBadge(icon_name, role, 34)
        top = hbox(self.label, None, self.badge)
        layout = vbox(top, self.value, self.sub, spacing=2, margins=(18, 16, 18, 16))
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)


class ColorDot(QWidget):
    def __init__(self, color: str, size: int = 10, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(self.rect().adjusted(0, 0, -1, -1))
        p.end()


class ElidedLabel(QLabel):
    """너비가 모자라면 말줄임표로 줄이는 라벨(전체 문구는 툴팁)."""

    def __init__(self, text: str = "", role: str | None = None, parent=None):
        super().__init__(parent)
        if role:
            self.setProperty("role", role)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setText(text)

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self._full) + 4, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        # 줄여야 할 때는 말줄임표로 줄인다
        return QSize(min(24, self.sizeHint().width()), super().minimumSizeHint().height())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, Qt.ElideRight, max(10, self.width())))

    def full_text(self) -> str:
        return self._full


# ---------------------------------------------------------------- 토글 스위치


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        on_theme_change(self, self.update)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def paintEvent(self, _event) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QColor(t.accent) if self.isChecked() else QColor(t.surface3 if not t.dark else t.border_strong)
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        x = 3 + self._offset * 18
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, 16, 16))
        p.end()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)


# ---------------------------------------------------------------- 세그먼트 컨트롤


class SegmentedControl(QFrame):
    changed = Signal(str)

    def __init__(self, options: Iterable[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for key, label in options:
            btn = QPushButton(label)
            btn.setProperty("variant", "segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, k=key: self._on_click(k))
            self.group.addButton(btn)
            self.buttons[key] = btn
            layout.addWidget(btn)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        if self.buttons:
            next(iter(self.buttons.values())).setChecked(True)

    def _on_click(self, key: str) -> None:
        self.changed.emit(key)

    def current(self) -> str:
        for key, btn in self.buttons.items():
            if btn.isChecked():
                return key
        return ""

    def set_current(self, key: str, emit: bool = False) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)
            if emit:
                self.changed.emit(key)

    def set_label(self, key: str, label: str) -> None:
        self.buttons[key].setText(label)


# ---------------------------------------------------------------- 자동 높이 텍스트 입력


class AutoTextEdit(QPlainTextEdit):
    """내용 줄 수만큼 높이가 늘어나는 여러 줄 입력(스크롤 없음)."""

    def __init__(self, placeholder: str = "", min_lines: int = 1, flat: bool = True, muted: bool = False, parent=None):
        super().__init__(parent)
        self.min_lines = min_lines
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if flat:
            self.setProperty("flat", True)
        if muted:
            self.setProperty("tone", "muted")
        self.document().setDocumentMargin(2)
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def event(self, e) -> bool:
        if e.type() in (QEvent.Polish, QEvent.StyleChange, QEvent.FontChange):
            QTimer.singleShot(0, self._fit)
        return super().event(e)

    def _fit(self) -> None:
        # QPlainTextDocumentLayout 의 문서 높이는 '줄 수'(줄바꿈 포함)다
        lines = max(self.min_lines, math.ceil(self.document().size().height()))
        margins = self.contentsMargins()
        height = lines * self.fontMetrics().lineSpacing() + 2 * self.document().documentMargin() + margins.top() + margins.bottom() + 4
        if self.height() != int(height):
            self.setFixedHeight(int(height))


# ---------------------------------------------------------------- 빈 상태


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, description: str = "", action: QPushButton | None = None, parent=None):
        super().__init__(parent)
        self.badge = IconBadge(icon_name, "accent", 52)
        self.title = make_label(title, "emptytitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.description = make_label(description, "muted", wrap=True)
        self.description.setAlignment(Qt.AlignCenter)
        self.description.setMaximumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(10)
        layout.addStretch(1)
        layout.addWidget(self.badge, 0, Qt.AlignHCenter)
        layout.addSpacing(4)
        layout.addWidget(self.title, 0, Qt.AlignHCenter)
        layout.addWidget(self.description, 0, Qt.AlignHCenter)
        if action:
            layout.addSpacing(6)
            layout.addWidget(action, 0, Qt.AlignHCenter)
        layout.addStretch(1)


# ---------------------------------------------------------------- 토스트


class ToastHost:
    """창 하단 가운데에 잠깐 떴다 사라지는 알림."""

    ICONS = {"info": ("info", "#FFFFFF"), "success": ("check-circle", "#4ADE80"), "error": ("alert", "#FF8A8A")}

    def __init__(self, parent: QWidget):
        self.parent = parent
        self.frame = QFrame(parent)
        self.frame.setObjectName("Toast")
        self.icon = QLabel(self.frame)
        self.text = QLabel(self.frame)
        self.text.setObjectName("ToastText")
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(520)
        layout = QHBoxLayout(self.frame)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(10)
        layout.addWidget(self.icon)
        layout.addWidget(self.text)
        self.effect = QGraphicsOpacityEffect(self.frame)
        self.frame.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self.frame)
        self.anim.setDuration(180)
        self.timer = QTimer(self.frame)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.frame.hide()

    def show(self, message: str, kind: str = "info", duration_ms: int = 3200) -> None:
        name, color = self.ICONS.get(kind, self.ICONS["info"])
        self.icon.setPixmap(render_pixmap(name, color, 18))
        self.text.setText(message)
        self.frame.adjustSize()
        self.reposition()
        self.frame.raise_()
        self.frame.show()
        self.anim.stop()
        self.anim.setStartValue(self.effect.opacity() if self.frame.isVisible() else 0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.timer.start(duration_ms)

    def hide(self) -> None:
        self.anim.stop()
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.start()
        QTimer.singleShot(200, self.frame.hide)

    def reposition(self) -> None:
        p = self.parent
        self.frame.adjustSize()
        x = (p.width() - self.frame.width()) // 2
        y = p.height() - self.frame.height() - 28
        self.frame.move(max(8, x), max(8, y))

    @property
    def message(self) -> str:
        return self.text.text() if self.frame.isVisible() else ""


# ---------------------------------------------------------------- 타임라인


class TimelineBar(QWidget):
    """하루 활동을 가로 막대 위의 색 구간으로 보여준다. 마우스를 올리면 상세 정보."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(66)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.day_start = 0.0
        self.start_hour = 8
        self.end_hour = 19
        self.segments: list[tuple[float, float, str, str]] = []  # (시작, 끝, 색, 설명)
        self.meetings: list[tuple[float, float, str]] = []
        self.show_now = False
        on_theme_change(self, self.update)

    def sizeHint(self) -> QSize:
        return QSize(600, 66)

    def set_data(self, day_start: float, segments, meetings, start_hour: int, end_hour: int, show_now: bool) -> None:
        self.day_start = day_start
        self.segments = list(segments)
        self.meetings = list(meetings)
        self.start_hour, self.end_hour = start_hour, max(start_hour + 1, end_hour)
        self.show_now = show_now
        self.update()

    def _track(self) -> QRectF:
        return QRectF(0, 6, self.width(), 30)

    def _x(self, ts: float) -> float:
        track = self._track()
        span = (self.end_hour - self.start_hour) * 3600
        rel = (ts - self.day_start - self.start_hour * 3600) / span
        return track.left() + max(0.0, min(1.0, rel)) * track.width()

    def paintEvent(self, _event) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = self._track()
        clip = QPainterPath()
        clip.addRoundedRect(track, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.surface2))
        p.drawPath(clip)
        p.save()
        p.setClipPath(clip)
        for start, end, color, _label in self.segments:
            x0, x1 = self._x(start), self._x(end)
            if x1 - x0 < 0.6:
                x1 = x0 + 0.6
            p.setBrush(QColor(color))
            p.drawRect(QRectF(x0, track.top(), x1 - x0, track.height()))
        for start, end, _title in self.meetings:
            x0, x1 = self._x(start), self._x(end)
            rect = QRectF(x0, track.top(), max(2.0, x1 - x0), track.height())
            p.setBrush(qcolor(t.meeting, 235))
            p.drawRect(rect)
            if rect.width() > 34:
                p.setPen(QColor("#FFFFFF"))
                p.setFont(font(11, QFont.DemiBold))
                p.drawText(rect, Qt.AlignCenter, "회의")
                p.setPen(Qt.NoPen)
        p.restore()

        # 시간 눈금
        p.setFont(font(11))
        hours = self.end_hour - self.start_hour
        step = 1 if self.width() / max(1, hours) > 46 else 2
        for h in range(self.start_hour, self.end_hour + 1, step):
            x = self._x(self.day_start + h * 3600)
            p.setPen(QPen(qcolor(t.border_strong), 1))
            p.drawLine(int(x), int(track.bottom()) + 2, int(x), int(track.bottom()) + 6)
            p.setPen(QColor(t.text3))
            label = f"{h:02d}"
            w = QFontMetrics(p.font()).horizontalAdvance(label)
            p.drawText(int(min(max(0, x - w / 2), self.width() - w)), int(track.bottom()) + 20, label)

        if self.show_now:
            now = datetime.now().timestamp()
            if self.day_start + self.start_hour * 3600 <= now <= self.day_start + self.end_hour * 3600:
                x = self._x(now)
                p.setPen(QPen(QColor(t.danger), 2))
                p.drawLine(int(x), int(track.top()) - 4, int(x), int(track.bottom()) + 2)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.danger))
                p.drawEllipse(QRectF(x - 3.5, track.top() - 8, 7, 7))
        p.end()

    def _hit(self, x: float) -> str:
        for start, end, title in self.meetings:
            if self._x(start) <= x <= max(self._x(end), self._x(start) + 2):
                return f"회의 · {title}\n{datetime.fromtimestamp(start):%H:%M} – {datetime.fromtimestamp(end):%H:%M}"
        for start, end, _color, label in self.segments:
            if self._x(start) <= x <= max(self._x(end), self._x(start) + 1):
                return f"{label}\n{datetime.fromtimestamp(start):%H:%M} – {datetime.fromtimestamp(end):%H:%M}"
        return ""

    def mouseMoveEvent(self, event) -> None:
        text = self._hit(event.position().x()) if self._track().contains(event.position()) else ""
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()


# ---------------------------------------------------------------- 사용량 막대


class _Bar(QWidget):
    def __init__(self, ratio: float, color: str, parent=None):
        super().__init__(parent)
        self.ratio, self.color = ratio, color
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        on_theme_change(self, self.update)

    def paintEvent(self, _event) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.surface2))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 8), 4, 4)
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(QRectF(0, 0, max(8.0, self.width() * self.ratio), 8), 4, 4)
        p.end()


class UsageList(QWidget):
    """앱별 사용 시간: 색 점 · 이름 · 막대 · 시간."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(12)
        self._rows = 0

    def set_rows(self, rows: list[tuple[str, str, float, str]]) -> None:
        """rows: (색, 이름, 비율 0~1, 표시 시간)"""
        clear_layout(self.layout_)
        self._rows = len(rows)
        for color, name, ratio, label in rows:
            row = QWidget()
            name_label = ElidedLabel(name)
            name_label.setMinimumWidth(80)
            value = make_label(label, "muted")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setFixedWidth(84)
            top = hbox(ColorDot(color, 9), name_label, value, spacing=8)
            col = vbox(top, _Bar(ratio, color), spacing=6)
            row.setLayout(col)
            self.layout_.addWidget(row)

    def row_count(self) -> int:
        return self._rows
