"""활성 창 샘플링 → 활동 세션 기록.

같은 (앱, 제목, 유휴 여부)가 이어지는 동안은 한 세션의 end_ts 만 늘린다.
유휴가 감지되면 마지막 입력 시각까지 거슬러 올라가 유휴 세션을 시작한다.
절전·최대 절전 등으로 샘플 간격이 크게 벌어지면 세션을 이어 붙이지 않는다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..config import Settings
from ..db import Database
from ..models import ActivitySession
from .base import PlatformProbe, WindowInfo
from .rules import categorize, mask, within_tracking_hours

log = logging.getLogger(__name__)

IDLE_APP = "(자리 비움)"
LOCKED_TITLE = "잠금 화면"
DESKTOP_APP = "(바탕 화면)"


@dataclass
class _Current:
    session_id: int
    key: tuple[str, str, bool]
    start_ts: float
    last_ts: float


class ActivityTracker:
    def __init__(
        self,
        db: Database,
        probe: PlatformProbe,
        settings: Callable[[], Settings],
        clock: Callable[[], float] = time.time,
    ):
        self.db = db
        self.probe = probe
        self._settings = settings
        self._clock = clock
        self._current: _Current | None = None
        self._paused = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_window: WindowInfo | None = None  # 회의 감지 등에서 참고 (가리기 전 원본)
        self.listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------ 제어
    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._close()

    def resume(self) -> None:
        self._paused = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="activity-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception:  # 수집 실패가 앱 전체를 멈추지 않도록
                log.exception("활동 샘플링 실패")
            self._stop.wait(max(0.5, self._settings().poll_interval_sec))

    # ------------------------------------------------------------ 샘플링
    def _gap_limit(self, settings: Settings) -> float:
        return max(30.0, settings.poll_interval_sec * 5)

    def _close(self) -> None:
        self._current = None

    def sample(self) -> None:
        settings = self._settings()
        now = self._clock()
        if self._paused or not within_tracking_hours(datetime.fromtimestamp(now), settings):
            self._close()
            return

        locked = self.probe.is_locked()
        idle_for = self.probe.idle_seconds()
        exe_path = ""
        if locked or idle_for >= settings.idle_threshold_sec:
            key = (IDLE_APP, LOCKED_TITLE if locked else "", True)
        else:
            win = self.probe.foreground_window()
            self.last_window = win
            if win is None:
                key = (DESKTOP_APP, "", False)
            else:
                shown = mask(win, settings)
                key = (shown.app_name, shown.window_title, False)
                exe_path = shown.exe_path

        cur = self._current
        contiguous = cur is not None and now - cur.last_ts <= self._gap_limit(settings)
        if contiguous and cur.key == key:
            cur.last_ts = now
            self.db.update_session_end(cur.session_id, now)
            return

        if not contiguous:
            start_ts = now  # 첫 샘플이거나 절전 등으로 끊긴 뒤
        elif key[2] and not cur.key[2]:
            # 활동 → 유휴: 마지막 입력 시각부터 유휴로 보고 앞 세션을 잘라낸다
            start_ts = min(max(cur.start_ts, now - idle_for), now)
            self.db.update_session_end(cur.session_id, start_ts)
        else:
            start_ts = cur.last_ts  # 빈틈 없이 이어 붙인다

        app, title, idle = key
        session = ActivitySession(
            start_ts=start_ts,
            end_ts=now,
            app_name=app,
            window_title=title,
            exe_path=exe_path,
            is_idle=idle,
            category="" if idle else categorize(app, title, settings.category_rules),
        )
        self._current = _Current(self.db.insert_session(session), key, start_ts, now)
        for listener in list(self.listeners):
            listener()
