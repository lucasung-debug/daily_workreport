"""회의 처리 파이프라인: 등록 → 음성 변환(STT) → 회의록 작성(Claude) → 액션아이템 저장.

단일 워커 스레드가 한 건씩 처리한다(로컬 Whisper 의 CPU 경합 방지).
앱을 다시 켜면 끝나지 않은 작업을 이어서 처리한다.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .. import paths
from ..analysis.claude_client import ClaudeError, ClaudeService
from ..config import Settings
from ..db import Database, local_date
from ..models import Meeting, MeetingStatus
from . import audio
from .detect import guess_title
from .minutes import action_items_from, generate_minutes
from .stt.base import STTError, Transcriber, create_transcriber, transcribe_recording

log = logging.getLogger(__name__)

Notify = Callable[[str, str, int], None]  # (제목, 내용, 회의 id)


class MeetingPipeline:
    def __init__(
        self,
        db: Database,
        settings: Callable[[], Settings],
        claude: ClaudeService,
        transcriber_factory: Callable[[Settings], Transcriber] = create_transcriber,
        notify: Notify | None = None,
    ):
        self.db = db
        self._settings = settings
        self.claude = claude
        self._transcriber_factory = transcriber_factory
        self.notify = notify
        self.listeners: list[Callable[[int], None]] = []
        self._queue: queue.Queue[tuple[int, bool] | None] = queue.Queue()
        self._queued: set[int] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.current: int | None = None

    # ------------------------------------------------------------ 제어
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        for meeting in self.db.pending_meetings():
            self.db.update_meeting(meeting.id, status=MeetingStatus.QUEUED, progress=0.0)
            self.enqueue(meeting.id)
        self._thread = threading.Thread(target=self._run, name="meeting-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, meeting_id: int, force_transcribe: bool = False) -> None:
        with self._lock:
            if meeting_id in self._queued:
                return
            self._queued.add(meeting_id)
        self._queue.put((meeting_id, force_transcribe))

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            meeting_id, force = item
            with self._lock:
                self._queued.discard(meeting_id)
            try:
                self.process(meeting_id, force_transcribe=force)
            except Exception:
                log.exception("회의 처리 중 예기치 않은 오류 (id=%s)", meeting_id)

    def _changed(self, meeting_id: int) -> None:
        for listener in list(self.listeners):
            try:
                listener(meeting_id)
            except Exception:
                log.exception("회의 변경 알림 실패")

    def _update(self, meeting_id: int, **fields) -> None:
        self.db.update_meeting(meeting_id, **fields)
        self._changed(meeting_id)

    # ------------------------------------------------------------ 등록
    def import_file(
        self,
        path: Path,
        source: str,
        started_at: float | None = None,
        ended_at: float | None = None,
        title: str = "",
    ) -> Meeting:
        path = Path(path)
        st = path.stat()
        duration = audio.probe_duration(path)
        end = ended_at or st.st_mtime
        start = started_at or (end - duration if duration else end)
        settings = self._settings()
        meeting = Meeting(
            date=local_date(start),
            title=title or guess_title(self.db, start, end, settings, path.stem),
            started_at=start,
            ended_at=max(end, start),
            source=source,
            audio_path=str(path),
        )
        self.db.create_meeting(meeting)
        self.db.mark_processed(str(path.resolve()), st.st_size, st.st_mtime, meeting.id)
        log.info("회의 등록: %s (%s)", meeting.title, path.name)
        self._changed(meeting.id)
        self.enqueue(meeting.id)
        return meeting

    # ------------------------------------------------------------ 처리
    def process(self, meeting_id: int, force_transcribe: bool = False) -> Meeting | None:
        meeting = self.db.get_meeting(meeting_id)
        if meeting is None:
            return None
        settings = self._settings()
        self.current = meeting_id
        try:
            if meeting.transcript is None or force_transcribe:
                meeting.transcript = self._transcribe(meeting, settings)

            if not meeting.transcript.segments:
                self._update(meeting_id, status=MeetingStatus.ERROR, error="인식된 음성이 없습니다. 녹음 장치와 파일을 확인하세요.")
                return self.db.get_meeting(meeting_id)

            if not self.claude.available():
                self._update(meeting_id, status=MeetingStatus.TRANSCRIBED, progress=1.0, error="Claude API 키가 없어 회의록 요약을 건너뛰었습니다.")
                self._notify(meeting_id, "음성 변환 완료", f"{meeting.title}: 전사문이 준비되었습니다. (회의록 요약은 API 키 설정 후 가능)")
                return self.db.get_meeting(meeting_id)

            self._update(meeting_id, status=MeetingStatus.SUMMARIZING, error="")
            minutes = generate_minutes(self.claude, meeting, settings)
            fields = {"minutes": minutes, "status": MeetingStatus.DONE, "progress": 1.0, "error": ""}
            if minutes.title and _is_auto_title(meeting.title):
                fields["title"] = minutes.title
            self.db.replace_action_items(meeting_id, action_items_from(minutes, meeting_id, settings.user_name))
            self._update(meeting_id, **fields)
            self._notify(meeting_id, "회의록 준비 완료", f"{fields.get('title', meeting.title)} 회의록이 작성되었습니다.")
        except (STTError, ClaudeError) as exc:
            log.warning("회의 처리 실패 (id=%s): %s", meeting_id, exc)
            self._update(meeting_id, status=MeetingStatus.ERROR, error=str(exc))
            self._notify(meeting_id, "회의 처리 실패", f"{meeting.title}: {exc}")
        except Exception as exc:
            log.exception("회의 처리 실패 (id=%s)", meeting_id)
            self._update(meeting_id, status=MeetingStatus.ERROR, error=f"처리 중 오류: {exc}")
        finally:
            self.current = None
        return self.db.get_meeting(meeting_id)

    def _transcribe(self, meeting: Meeting, settings: Settings):
        transcriber = self._transcriber_factory(settings)
        self._update(meeting.id, status=MeetingStatus.TRANSCRIBING, progress=0.0, error="", stt_engine=transcriber.name)
        last = [0.0, time.monotonic()]

        def progress(p: float) -> None:
            # DB·UI 갱신은 2% 또는 2초마다
            if p - last[0] >= 0.02 or time.monotonic() - last[1] >= 2 or p >= 1.0:
                last[0], last[1] = p, time.monotonic()
                self._update(meeting.id, progress=round(min(p, 1.0), 3))

        work_dir = paths.audio_dir() / "tmp"
        work_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcribe_recording(transcriber, Path(meeting.audio_path), "ko", progress, work_dir=work_dir)
        self._update(meeting.id, transcript=transcript, status=MeetingStatus.TRANSCRIBED, progress=1.0)
        return transcript

    def _notify(self, meeting_id: int, title: str, message: str) -> None:
        if self.notify:
            try:
                self.notify(title, message, meeting_id)
            except Exception:
                log.exception("알림 실패")

    # ------------------------------------------------------------ 정리
    def cleanup_old_audio(self, now: float | None = None) -> int:
        """보관 기간이 지난 '앱 내장 녹음' 파일을 지운다. 녹음기 앱 폴더의 원본은 건드리지 않는다."""
        settings = self._settings()
        if settings.audio_retention_days <= 0:
            return 0
        cutoff = (now or time.time()) - settings.audio_retention_days * 86400
        own_dir = paths.audio_dir().resolve()
        removed = 0
        for meeting in self.db.meetings_with_audio_before(cutoff):
            if meeting.source != "in_app" or meeting.status in MeetingStatus.PENDING:
                continue
            path = Path(meeting.audio_path)
            try:
                if own_dir in path.resolve().parents:
                    path.unlink(missing_ok=True)
                    self.db.update_meeting(meeting.id, audio_path="")
                    removed += 1
            except OSError as exc:
                log.warning("오디오 삭제 실패(%s): %s", path, exc)
        return removed


def _is_auto_title(title: str) -> bool:
    return not title or title.startswith("회의 ") and title[3:].replace(":", "").isdigit()
