"""회의 감지와 회의 제목 추정.

- Windows 11 은 앱별 마이크 사용 기록을 레지스트리(CapabilityAccessManager)에 남긴다.
  LastUsedTimeStop 이 0 이면 지금 마이크를 쓰는 중이다.
- 마이크를 쓰는 앱이나 전면 창이 회의 앱(Teams, Zoom 등)이면 "녹음할까요?" 알림을 띄운다.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from datetime import datetime
from typing import Callable

from ..analysis.aggregate import clean_title
from ..collector.base import WindowInfo
from ..config import Settings
from ..db import Database

log = logging.getLogger(__name__)

MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

_MEETING_SUFFIX = re.compile(
    r"\s*[|\-–—]\s*(Microsoft Teams.*|Teams|Zoom( Meeting| Workplace| 회의)?|Webex.*|Google Meet|Meet|Slack)\s*$",
    re.IGNORECASE,
)
_GENERIC_TITLES = re.compile(r"^(Microsoft Teams|Zoom( Meeting| Workplace| 회의)?|Zoom|Webex|Meet|회의|Meeting|채팅|Chat)$", re.IGNORECASE)
_DEFAULT_RECORDING_NAME = re.compile(
    r"^((녹음|새 녹음|사운드 녹음|Recording|Sound recording|New recording)( ?\(\d+\))?|\d{8}[ _-]?\d{4,6}|\d{4}-\d{2}-\d{2}[ _]\d{2}[-.]\d{2}([-.]\d{2})?)$",
    re.IGNORECASE,
)


def mic_in_use_apps() -> list[str]:
    """현재 마이크를 사용 중인 앱 목록(레지스트리 키 이름). Windows 외에서는 빈 목록."""
    if sys.platform != "win32":
        return []
    import winreg

    apps: list[str] = []

    def scan(key, prefix: str = "") -> None:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(key, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(key, name) as sub:
                    if name == "NonPackaged":
                        scan(sub, "")
                        continue
                    try:
                        stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                        start, _ = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                    except OSError:
                        continue
                    if start and stop == 0:
                        apps.append(prefix + name.replace("#", "\\"))
            except OSError:
                continue

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY) as root:
            scan(root)
    except OSError as exc:
        log.debug("마이크 사용 기록을 읽지 못했습니다: %s", exc)
    return apps


def matches_meeting_app(text: str, settings: Settings) -> bool:
    text = text.lower()
    return any(app.strip() and app.strip().lower() in text for app in settings.meeting_apps)


def is_meeting_window(win: WindowInfo | None, settings: Settings) -> bool:
    if win is None:
        return False
    return matches_meeting_app(f"{win.exe_name} {win.app_name} {win.window_title}", settings)


def title_from_window(title: str) -> str:
    title = _MEETING_SUFFIX.sub("", clean_title(title)).strip(" |-")
    return "" if not title or _GENERIC_TITLES.match(title) else title


def title_from_filename(stem: str) -> str:
    stem = stem.strip()
    if stem.endswith("_dual"):
        return ""
    return "" if _DEFAULT_RECORDING_NAME.match(stem) else stem


def guess_title(db: Database, start_ts: float, end_ts: float, settings: Settings, filename_stem: str = "") -> str:
    from_name = title_from_filename(filename_stem) if filename_stem else ""
    if from_name:
        return from_name
    best, best_sec = "", 0.0
    for s in db.sessions_between(start_ts, end_ts):
        if s.is_idle or not matches_meeting_app(f"{s.app_name} {s.window_title} {s.exe_path}", settings):
            continue
        title = title_from_window(s.window_title)
        if title and s.duration > best_sec:
            best, best_sec = title, s.duration
    return best or f"회의 {datetime.fromtimestamp(start_ts):%H:%M}"


class MeetingDetector:
    """마이크가 회의 앱에서 켜지면 한 번 알린다. 마이크가 꺼질 때까지 다시 알리지 않는다."""

    def __init__(
        self,
        settings: Callable[[], Settings],
        foreground: Callable[[], WindowInfo | None],
        is_recording: Callable[[], bool],
        on_detect: Callable[[str], None],
        mic_apps: Callable[[], list[str]] = mic_in_use_apps,
        interval: float = 15.0,
    ):
        self._settings = settings
        self._foreground = foreground
        self._is_recording = is_recording
        self._on_detect = on_detect
        self._mic_apps = mic_apps
        self.interval = interval
        self._notified = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> bool:
        settings = self._settings()
        if not settings.meeting_detect_enabled:
            return False
        apps = [a for a in self._mic_apps() if "workreport" not in a.lower()]
        if not apps:
            self._notified = False
            return False
        if self._notified or self._is_recording():
            return False
        win = self._foreground()
        if any(matches_meeting_app(a, settings) for a in apps) or is_meeting_window(win, settings):
            self._notified = True
            self._on_detect(title_from_window(win.window_title) if win else "")
            return True
        return False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="meeting-detector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.check()
            except Exception:
                log.exception("회의 감지 실패")
