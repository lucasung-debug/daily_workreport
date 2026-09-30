"""Windows 11 녹음기 앱 저장 폴더 감시.

- 녹음기 앱은 녹음을 멈추면 문서\\Sound Recordings 에 m4a 파일을 남긴다(설정에서 폴더 변경 가능).
- 파일 크기·수정 시각이 일정 시간 변하지 않으면 녹음이 끝난 것으로 보고 회의로 등록한다.
- 처음 켤 때 폴더에 있던 예전 녹음은 가져오지 않는다(recorder_watch_since 이후 파일만).
- 녹음기 앱에서 파일 이름을 바꾸면(크기·수정 시각 동일) 새 파일로 보지 않고 기존 회의의 경로·제목만 갱신한다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..db import Database
from .detect import title_from_filename

log = logging.getLogger(__name__)

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".flac", ".wma", ".aac", ".ogg"}


@dataclass
class _Pending:
    size: int
    mtime: float
    stable_since: float


class RecorderFolderWatcher:
    def __init__(
        self,
        db: Database,
        settings: Callable[[], Settings],
        on_new_file: Callable[[Path], None],
        persist_settings: Callable[[Settings], None] | None = None,
        stable_sec: float = 8.0,
        interval: float = 5.0,
        clock: Callable[[], float] = time.time,
    ):
        self.db = db
        self._settings = settings
        self._on_new_file = on_new_file
        self._persist = persist_settings
        self.stable_sec = stable_sec
        self.interval = interval
        self._clock = clock
        self._pending: dict[str, _Pending] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="recorder-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.scan()
            except Exception:
                log.exception("녹음 폴더 감시 실패")

    def scan(self) -> list[Path]:
        settings = self._settings()
        if not settings.recorder_watch_enabled:
            return []
        now = self._clock()
        if not settings.recorder_watch_since:
            settings.recorder_watch_since = now
            if self._persist:
                self._persist(settings)
        folder = settings.recorder_path()
        if not folder.is_dir():
            return []

        dispatched: list[Path] = []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in AUDIO_EXTS or not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < settings.recorder_watch_since:
                continue
            key = str(path.resolve())
            if self.db.is_processed(key):
                continue
            renamed = self.db.processed_by_signature(st.st_size, st.st_mtime)
            if renamed and not Path(renamed[0]).exists():
                self._handle_rename(key, path, st, renamed[1])
                continue

            prev = self._pending.get(key)
            if prev is None or prev.size != st.st_size or prev.mtime != st.st_mtime:
                self._pending[key] = _Pending(st.st_size, st.st_mtime, now)
                continue
            if now - prev.stable_since < self.stable_sec or st.st_size == 0 or not _readable(path):
                continue
            del self._pending[key]
            self._on_new_file(path)
            dispatched.append(path)
        return dispatched

    def _handle_rename(self, key: str, path: Path, st, meeting_id: int | None) -> None:
        self.db.mark_processed(key, st.st_size, st.st_mtime, meeting_id)
        if meeting_id is None:
            return
        fields: dict = {"audio_path": str(path)}
        title = title_from_filename(path.stem)
        if title:
            fields["title"] = title
        self.db.update_meeting(meeting_id, **fields)
        log.info("녹음 파일 이름 변경 반영: %s", path.name)


def _readable(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            fh.read(1)
        return True
    except OSError:
        return False
