"""시스템 트레이: 열기 / 회의 녹음 / 할 일 추가 / 빠른 메모 / 기록 일시정지 / 초안 생성 / 종료."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMenu, QSystemTrayIcon

from ..models import format_hms
from ..services import Services
from .common import app_icon


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        services: Services,
        on_open: Callable[[], None],
        on_toggle_recording: Callable[[], None],
        on_generate: Callable[[], None],
        on_quit: Callable[[], None],
        parent=None,
    ):
        super().__init__(app_icon(), parent)
        self.services = services
        self._message_click: Callable[[], None] | None = None
        self._icon_state: tuple[bool, bool] | None = None

        menu = QMenu()
        self.act_open = QAction("WorkReport 열기", menu)
        self.act_open.triggered.connect(on_open)
        self.act_record = QAction("● 회의 녹음 시작", menu)
        self.act_record.triggered.connect(on_toggle_recording)
        self.act_todo = QAction("할 일 추가…", menu)
        self.act_todo.triggered.connect(self.quick_todo)
        self.act_note = QAction("빠른 메모…", menu)
        self.act_note.triggered.connect(self.quick_note)
        self.act_pause = QAction("기록 일시정지", menu)
        self.act_pause.triggered.connect(self.toggle_pause)
        self.act_generate = QAction("오늘 업무일지 초안 만들기", menu)
        self.act_generate.triggered.connect(on_generate)
        self.act_quit = QAction("종료", menu)
        self.act_quit.triggered.connect(on_quit)
        for act in (self.act_open, None, self.act_record, self.act_todo, self.act_note, self.act_pause, self.act_generate, None, self.act_quit):
            if act is None:
                menu.addSeparator()
            else:
                menu.addAction(act)
        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(lambda reason: on_open() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.messageClicked.connect(self._on_message_clicked)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        recording = self.services.recorder.is_recording
        paused = self.services.paused
        if self._icon_state != (recording, paused):  # 상태가 바뀔 때만 다시 그린다(깜빡임 방지)
            self._icon_state = (recording, paused)
            self.setIcon(app_icon(recording=recording, paused=paused))
        self.act_record.setText(f"■ 회의 녹음 중지 ({format_hms(self.services.recorder.elapsed())})" if recording else "● 회의 녹음 시작")
        self.act_pause.setText("기록 다시 시작" if paused else "기록 일시정지")
        state = "회의 녹음 중" if recording else ("기록 일시정지" if paused else "활동 기록 중")
        self.setToolTip(f"WorkReport — {state}")

    def toggle_pause(self) -> None:
        self.services.set_paused(not self.services.paused)
        self.refresh()

    def quick_todo(self) -> None:
        text, ok = QInputDialog.getText(None, "할 일 추가", "오늘 할 일 (오늘 화면 맨 위 목록에 들어가요)")
        if ok and text.strip():
            self.services.todos.add(text)
            self.notify("할 일 추가", f"‘{text.strip()}’ 할 일을 추가했어요.")

    def quick_note(self) -> None:
        text, ok = QInputDialog.getMultiLineText(None, "빠른 메모", "오늘 업무일지 초안에 반영할 메모")
        if ok and text.strip():
            self.services.db.add_note(text.strip())
            self.notify("메모 저장", "오늘 업무일지 초안에 반영됩니다.")

    def notify(self, title: str, message: str, on_click: Callable[[], None] | None = None) -> None:
        self._message_click = on_click
        self.showMessage(title, message, QSystemTrayIcon.Information, 8000)

    def _on_message_clicked(self) -> None:
        if self._message_click:
            callback, self._message_click = self._message_click, None
            callback()
