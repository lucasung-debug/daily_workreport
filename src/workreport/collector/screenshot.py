"""(옵션, 기본 OFF) 주기 스크린샷 → Claude 비전 캡션.

- 자리 비움·잠금·제외 대상 창이면 캡처하지 않는다.
- 캡션을 만든 뒤 원본 이미지는 기본적으로 삭제한다(keep_screenshot_images 로 보관 가능).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import paths
from ..config import Settings
from ..db import Database
from ..models import Screenshot
from .base import PlatformProbe
from .rules import is_excluded, within_tracking_hours

log = logging.getLogger(__name__)

MAX_WIDTH = 1280

# (image_path, app_name, window_title) -> caption
CaptionFn = Callable[[Path, str, str], str]


def capture_primary_screen(dest: Path) -> Path:
    import mss
    from PIL import Image

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=70)
    return dest


class ScreenshotService:
    def __init__(
        self,
        db: Database,
        probe: PlatformProbe,
        settings: Callable[[], Settings],
        caption_fn: Callable[[], CaptionFn | None],
        capture: Callable[[Path], Path] = capture_primary_screen,
    ):
        self.db = db
        self.probe = probe
        self._settings = settings
        self._caption_fn = caption_fn
        self._capture = capture
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="screenshots", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(max(60, self._settings().screenshot_interval_min * 60)):
            try:
                self.tick()
            except Exception:
                log.exception("스크린샷 처리 실패")

    def tick(self) -> Screenshot | None:
        settings = self._settings()
        caption = self._caption_fn()
        if not settings.screenshot_enabled or self.paused or caption is None:
            return None
        now = time.time()
        if not within_tracking_hours(datetime.fromtimestamp(now), settings):
            return None
        if self.probe.is_locked() or self.probe.idle_seconds() >= settings.idle_threshold_sec:
            return None
        win = self.probe.foreground_window()
        if win is None or is_excluded(win, settings):
            return None

        stamp = datetime.fromtimestamp(now)
        dest = paths.screenshots_dir() / stamp.strftime("%Y-%m-%d") / f"{stamp:%H%M%S}.jpg"
        path = self._capture(dest)
        shot = Screenshot(ts=now, app_name=win.app_name, window_title=win.window_title, image_path=str(path))
        self.db.add_screenshot(shot)
        try:
            shot.caption = caption(path, win.app_name, win.window_title)
            shot.status = "done"
        except Exception as exc:
            log.warning("스크린샷 캡션 실패: %s", exc)
            shot.status = "error"
        if not settings.keep_screenshot_images:
            path.unlink(missing_ok=True)
            shot.image_path = ""
        self.db.update_screenshot(shot.id, caption=shot.caption, status=shot.status, image_path=shot.image_path)
        return shot
