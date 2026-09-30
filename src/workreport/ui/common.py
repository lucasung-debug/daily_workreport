"""UI 공통: 백그라운드 실행, 스레드→UI 신호 브리지, 아이콘, 업무 항목 표 편집기."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import ReportItem

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 백그라운드 실행


class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _TaskSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            log.exception("백그라운드 작업 실패")
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(result)


_alive: set[_TaskSignals] = set()


def run_async(fn: Callable, *args, on_done: Callable | None = None, on_error: Callable[[str], None] | None = None, **kwargs) -> None:
    """fn 을 스레드 풀에서 실행하고 결과를 UI 스레드의 콜백으로 돌려준다."""
    task = _Task(fn, args, kwargs)
    signals = task.signals
    _alive.add(signals)
    if on_done:
        signals.done.connect(on_done)
    if on_error:
        signals.failed.connect(on_error)
    signals.done.connect(lambda _r: _alive.discard(signals))
    signals.failed.connect(lambda _e: _alive.discard(signals))
    QThreadPool.globalInstance().start(task)


class Bridge(QObject):
    """백그라운드 스레드의 이벤트를 UI 스레드로 넘기는 신호 모음."""

    meeting_changed = Signal(int)
    activity_changed = Signal()
    notify = Signal(str, str)
    meeting_detected = Signal(str)
    recorder_error = Signal(str)
    report_ready = Signal(str)


# ---------------------------------------------------------------- 아이콘

ACCENT = "#2563eb"
RECORDING = "#dc2626"


def app_icon(recording: bool = False, paused: bool = False) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#6b7280" if paused else ACCENT))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("white"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(34)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, "W")
    if recording:
        p.setPen(QColor("white"))
        p.setBrush(QColor(RECORDING))
        p.drawEllipse(36, 36, 26, 26)
    p.end()
    icon = QIcon(pm)
    icon.addPixmap(pm.scaled(QSize(32, 32), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return icon


# ---------------------------------------------------------------- 업무 항목 표


class _MultilineDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setTabChangesFocus(True)
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.EditRole) or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText().strip(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        rect.setHeight(max(rect.height(), 90))
        editor.setGeometry(rect)


class ItemTableEditor(QWidget):
    """금일 실적 / 명일 계획 / 이슈 편집 표."""

    changed = Signal()
    COLUMNS = ["제목", "세부 내용", "시간(분)", "분류"]

    def __init__(self, show_time: bool = True, parent=None):
        super().__init__(parent)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setItemDelegateForColumn(1, _MultilineDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(2, not show_time)
        self.table.itemChanged.connect(self._on_item_changed)

        buttons = QHBoxLayout()
        for label, slot in (("추가", self.add_row), ("삭제", self.remove_row), ("▲", lambda: self.move(-1)), ("▼", lambda: self.move(1))):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    def _on_item_changed(self, _item) -> None:
        self.table.resizeRowsToContents()
        self.changed.emit()

    def set_items(self, items: list[ReportItem]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for item in items:
            self._append(item)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()

    def _append(self, item: ReportItem) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [item.title, item.detail, str(item.time_spent_min or ""), item.category]
        for col, value in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(value))

    def items(self) -> list[ReportItem]:
        out = []
        for row in range(self.table.rowCount()):
            cell = lambda c: (self.table.item(row, c).text().strip() if self.table.item(row, c) else "")
            title = cell(0)
            if not title:
                continue
            try:
                minutes = int(cell(2) or 0)
            except ValueError:
                minutes = 0
            out.append(ReportItem(title=title, detail=cell(1), time_spent_min=minutes, category=cell(3)))
        return out

    def add_row(self) -> None:
        self._append(ReportItem(title=""))
        row = self.table.rowCount() - 1
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))
        self.changed.emit()

    def remove_row(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.changed.emit()

    def move(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        items = self.items_raw()
        items[row], items[target] = items[target], items[row]
        self.table.blockSignals(True)
        for r, values in enumerate(items):
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.blockSignals(False)
        self.table.setCurrentCell(target, 0)
        self.table.resizeRowsToContents()
        self.changed.emit()

    def items_raw(self) -> list[list[str]]:
        return [
            [(self.table.item(r, c).text() if self.table.item(r, c) else "") for c in range(self.table.columnCount())]
            for r in range(self.table.rowCount())
        ]


def ro_item(text: str, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if align_right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item
