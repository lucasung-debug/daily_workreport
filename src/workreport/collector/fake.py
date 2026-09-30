"""테스트·비 Windows 개발용 가짜 프로브."""

from __future__ import annotations

from .base import WindowInfo


class FakeProbe:
    def __init__(self, window: WindowInfo | None = None, idle: float = 0.0, locked: bool = False):
        self.window = window
        self.idle = idle
        self.locked = locked

    def foreground_window(self) -> WindowInfo | None:
        return self.window

    def idle_seconds(self) -> float:
        return self.idle

    def is_locked(self) -> bool:
        return self.locked
