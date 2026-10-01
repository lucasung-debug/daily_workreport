"""UI 공통: 백그라운드 실행, 스레드→UI 신호 브리지, 트레이·창 아이콘."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap


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
    notify = Signal(str, str, int)
    meeting_detected = Signal(str)
    recorder_error = Signal(str)
    report_ready = Signal(str)


# ---------------------------------------------------------------- 아이콘

ACCENT = "#2F6BFF"
RECORDING = "#E5484D"


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
