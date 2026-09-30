import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from audio_util import tone, write_m4a, write_wav
from fakes import FakeClient, parsed
from workreport import paths
from workreport.analysis.claude_client import ClaudeService
from workreport.collector.base import WindowInfo
from workreport.config import Settings
from workreport.meeting.detect import MeetingDetector, guess_title, title_from_filename, title_from_window
from workreport.meeting.minutes import action_items_from, build_minutes_input
from workreport.meeting.pipeline import MeetingPipeline
from workreport.meeting.recorder import MeetingRecorder
from workreport.meeting.recorder_watch import RecorderFolderWatcher
from workreport.meeting.stt.fake import FakeTranscriber
from workreport.models import ActionItemDraft, ActivitySession, Meeting, MeetingMinutes, MeetingStatus, Segment, Transcript


def minutes(**kw):
    base = dict(
        title="배포 일정 회의",
        summary="금요일 배포 확정",
        attendees=["홍길동"],
        discussion=[],
        decisions=["배포는 금요일"],
        action_items=[
            ActionItemDraft(owner="홍길동", task="릴리스 노트 작성", due="2026-10-01", is_mine=False),
            ActionItemDraft(owner="김철수", task="QA 요청", due="", is_mine=False),
            ActionItemDraft(owner="나", task="배포 체크리스트", due="", is_mine=False),
        ],
        open_questions=[],
    )
    base.update(kw)
    return MeetingMinutes(**base)


# ---------------------------------------------------------------- detect / title


def test_title_helpers():
    assert title_from_window("주간회의 | Microsoft Teams") == "주간회의"
    assert title_from_window("Microsoft Teams") == ""
    assert title_from_window("Zoom 회의") == ""
    assert title_from_filename("녹음") == "" and title_from_filename("녹음 (3)") == "" and title_from_filename("Recording (2)") == ""
    assert title_from_filename("20260930_140000") == ""
    assert title_from_filename("고객사 킥오프") == "고객사 킥오프"


def test_guess_title_from_sessions(db):
    start = datetime(2026, 9, 30, 14, 0).timestamp()
    db.insert_session(ActivitySession(start, start + 600, "Microsoft Teams", "채팅 | Microsoft Teams"))
    db.insert_session(ActivitySession(start + 600, start + 3000, "Microsoft Teams", "제품 로드맵 리뷰 | Microsoft Teams"))
    db.insert_session(ActivitySession(start + 3000, start + 3600, "Excel", "로드맵.xlsx"))
    s = Settings()
    assert guess_title(db, start, start + 3600, s) == "제품 로드맵 리뷰"
    assert guess_title(db, start, start + 3600, s, "고객 미팅") == "고객 미팅"
    assert guess_title(db, start + 7200, start + 9000, s) == "회의 16:00"


def test_detector_notifies_once_per_mic_session():
    mic = [["C:\\Program Files\\Zoom\\bin\\Zoom.exe"]]
    hits = []
    det = MeetingDetector(lambda: Settings(), lambda: WindowInfo("Zoom", "Zoom 회의", "Zoom.exe"), lambda: False, hits.append, mic_apps=lambda: mic[0])
    assert det.check() and not det.check()
    mic[0] = []
    det.check()  # 마이크 꺼짐 → 재무장
    mic[0] = ["MSTeams_8wekyb3d8bbwe"]
    assert det.check()
    assert len(hits) == 2


def test_detector_ignores_non_meeting_and_while_recording():
    hits = []
    det = MeetingDetector(lambda: Settings(), lambda: WindowInfo("메모장", "a.txt", "notepad.exe"), lambda: False, hits.append, mic_apps=lambda: ["C:#Windows#dictation.exe"])
    assert not det.check()
    det2 = MeetingDetector(lambda: Settings(), lambda: None, lambda: True, hits.append, mic_apps=lambda: ["Zoom.exe"])
    assert not det2.check()
    det3 = MeetingDetector(lambda: Settings(meeting_detect_enabled=False), lambda: None, lambda: False, hits.append, mic_apps=lambda: ["Zoom.exe"])
    assert not det3.check()
    assert hits == []


# ---------------------------------------------------------------- minutes


def test_action_items_mine_detection():
    items = action_items_from(minutes(), 7, user_name="홍길동")
    assert [(i.task, i.is_mine) for i in items] == [("릴리스 노트 작성", True), ("QA 요청", False), ("배포 체크리스트", True)]
    assert all(i.meeting_id == 7 for i in items)


def test_minutes_input():
    m = Meeting(date="2026-09-30", title="주간회의", started_at=datetime(2026, 9, 30, 14).timestamp(), ended_at=datetime(2026, 9, 30, 14, 30).timestamp(), source="import", audio_path="")
    m.transcript = Transcript(segments=[Segment(start=61, end=63, text="시작합니다", speaker="나")])
    text = build_minutes_input(m, Settings(user_name="홍길동"))
    assert "# 회의 날짜: 2026-09-30 (수) 14:00~14:30 (30분)" in text
    assert "# 기록자 이름: 홍길동" in text
    assert "[00:01:01] 나: 시작합니다" in text


# ---------------------------------------------------------------- recorder folder watcher


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def test_watcher_imports_new_stable_files(db, tmp_path):
    folder = tmp_path / "Sound Recordings"
    folder.mkdir()
    old = write_wav(folder / "옛날 녹음.wav", tone(1))
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    settings = Settings(recorder_dir=str(folder))
    saved, found = [], []
    clock = Clock()
    watcher = RecorderFolderWatcher(db, lambda: settings, found.append, persist_settings=saved.append, stable_sec=8, clock=clock)

    assert watcher.scan() == []
    assert saved and settings.recorder_watch_since == clock.t  # 첫 실행 시각 기록

    new = write_m4a(folder / "녹음.m4a")
    os.utime(new, (clock.t + 1, clock.t + 1))
    assert watcher.scan() == []  # 처음 본 파일은 안정화 대기
    clock.t += 3
    assert watcher.scan() == []
    clock.t += 10
    assert watcher.scan() == [new]
    assert found == [new]
    db.mark_processed(str(new.resolve()), new.stat().st_size, new.stat().st_mtime, None)
    clock.t += 10
    assert watcher.scan() == []  # 이미 처리됨, 예전 파일은 무시


def test_watcher_handles_rename(db, tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    settings = Settings(recorder_dir=str(folder), recorder_watch_since=1.0)
    f = write_wav(folder / "녹음.wav", tone(1))
    start = time.time()
    mid = db.create_meeting(Meeting(date="2026-09-30", title="회의 14:00", started_at=start, ended_at=start + 1, source="recorder_app", audio_path=str(f)))
    st = f.stat()
    db.mark_processed(str(f.resolve()), st.st_size, st.st_mtime, mid)
    renamed = f.rename(folder / "고객사 킥오프.wav")
    watcher = RecorderFolderWatcher(db, lambda: settings, lambda p: pytest.fail("renamed file must not be imported"))
    assert watcher.scan() == []
    meeting = db.get_meeting(mid)
    assert meeting.audio_path == str(renamed) and meeting.title == "고객사 킥오프"


def test_watcher_disabled_or_missing_folder(db, tmp_path):
    watcher = RecorderFolderWatcher(db, lambda: Settings(recorder_watch_enabled=False), lambda p: None)
    assert watcher.scan() == []
    watcher = RecorderFolderWatcher(db, lambda: Settings(recorder_dir=str(tmp_path / "none"), recorder_watch_since=1), lambda p: None)
    assert watcher.scan() == []


# ---------------------------------------------------------------- in-app recorder


class FakeRecorderCtx:
    def __init__(self, level):
        self.level = level

    def record(self, numframes):
        time.sleep(numframes / 16000 / 4)  # 실제보다 빠르게
        return np.full((numframes, 1), self.level, np.float32)


class FakeSource:
    def __init__(self, level, fail=False):
        self.level, self.fail = level, fail

    @contextmanager
    def recorder(self, samplerate, channels, blocksize):
        assert samplerate == 16000 and channels == 1
        if self.fail:
            raise RuntimeError("no device")
        yield FakeRecorderCtx(self.level)


class FakeBackend:
    def __init__(self, mic_fail=False):
        self.mic_fail = mic_fail

    def microphone(self, name=""):
        return FakeSource(0.1, self.mic_fail)

    def loopback(self, name=""):
        return FakeSource(0.2)


def test_recorder_dual_channel(tmp_path):
    rec = MeetingRecorder(lambda: Settings(record_split_channels=True), backend=FakeBackend())
    path = rec.start()
    assert rec.is_recording and path.name.endswith("_dual.flac")
    assert paths.audio_dir() in path.parents
    time.sleep(0.6)
    out, start, end = rec.stop()
    assert not rec.is_recording and end >= start
    data, rate = sf.read(str(out))
    assert rate == 16000 and data.shape[1] == 2 and data.shape[0] > 1600
    assert np.allclose(data[:, 0], 0.1, atol=1e-3) and np.allclose(data[:, 1], 0.2, atol=1e-3)


def test_recorder_mono_mix(tmp_path):
    rec = MeetingRecorder(lambda: Settings(record_split_channels=False), backend=FakeBackend())
    rec.start()
    time.sleep(0.4)
    out, _, _ = rec.stop()
    data, _ = sf.read(str(out))
    assert data.ndim == 1 and np.allclose(data, 0.3, atol=1e-3)
    assert "_dual" not in out.name


def test_recorder_survives_device_failure(tmp_path):
    errors = []
    rec = MeetingRecorder(lambda: Settings(), backend=FakeBackend(mic_fail=True))
    rec.on_error = errors.append
    rec.start()
    time.sleep(0.5)
    out, _, _ = rec.stop()
    data, _ = sf.read(str(out))
    assert np.allclose(data[:, 0], 0) and np.allclose(data[:, 1], 0.2, atol=1e-3)  # 마이크 쪽은 무음으로 채움
    assert errors and "no device" in errors[0]


# ---------------------------------------------------------------- pipeline


def make_pipeline(db, client=None, transcriber=None, settings=None, configured=True):
    s = settings or Settings(user_name="홍길동")
    client = client or FakeClient(default=parsed(minutes()))
    claude = ClaudeService(lambda: s, client_factory=lambda: client, configured=lambda: configured)
    notes = []
    pipe = MeetingPipeline(db, lambda: s, claude, transcriber_factory=lambda _s: transcriber or FakeTranscriber(), notify=lambda t, m: notes.append((t, m)))
    return pipe, client, notes


def test_pipeline_full_flow(db, tmp_path):
    pipe, client, notes = make_pipeline(db)
    changes = []
    pipe.listeners.append(changes.append)
    wav = write_wav(tmp_path / "녹음.wav", tone(2))
    meeting = pipe.import_file(wav, "recorder_app")
    assert meeting.title.startswith("회의 ")
    assert abs(meeting.duration_sec - 2.0) < 0.05
    assert db.is_processed(str(wav.resolve()))

    result = pipe.process(meeting.id)
    assert result.status == MeetingStatus.DONE and result.progress == 1.0
    assert result.title == "배포 일정 회의"  # 자동 제목은 회의록 제목으로 교체
    assert result.transcript.engine == "fake" and len(result.transcript.segments) == 2
    assert result.minutes.decisions == ["배포는 금요일"]
    assert [a.task for a, _ in db.open_my_action_items()] == ["릴리스 노트 작성", "배포 체크리스트"]
    assert notes[-1][0] == "회의록 준비 완료"
    assert changes.count(meeting.id) >= 4
    assert "릴리스 노트는 제가 목요일까지" in client.calls[0][1]["messages"][0]["content"]


def test_pipeline_keeps_user_title(db, tmp_path):
    pipe, _, _ = make_pipeline(db)
    meeting = pipe.import_file(write_wav(tmp_path / "고객사 킥오프.wav", tone(1)), "recorder_app")
    assert meeting.title == "고객사 킥오프"
    assert pipe.process(meeting.id).title == "고객사 킥오프"


def test_pipeline_without_claude(db, tmp_path):
    pipe, client, notes = make_pipeline(db, configured=False)
    meeting = pipe.import_file(write_wav(tmp_path / "a.wav", tone(1)), "import")
    result = pipe.process(meeting.id)
    assert result.status == MeetingStatus.TRANSCRIBED and result.minutes is None
    assert client.calls == []
    assert "API 키" in result.error


def test_pipeline_errors(db, tmp_path):
    pipe, _, notes = make_pipeline(db, transcriber=FakeTranscriber(error="모델 없음"))
    meeting = pipe.import_file(write_wav(tmp_path / "a.wav", tone(1)), "import")
    result = pipe.process(meeting.id)
    assert result.status == MeetingStatus.ERROR and result.error == "모델 없음"
    assert notes[-1][0] == "회의 처리 실패"

    pipe2, _, _ = make_pipeline(db, transcriber=FakeTranscriber(segments=[]))
    m2 = pipe2.import_file(write_wav(tmp_path / "b.wav", tone(1)), "import")
    assert "인식된 음성이 없습니다" in pipe2.process(m2.id).error


def test_pipeline_resummarize_skips_stt(db, tmp_path):
    transcriber = FakeTranscriber()
    pipe, client, _ = make_pipeline(db, transcriber=transcriber)
    meeting = pipe.import_file(write_wav(tmp_path / "a.wav", tone(1)), "import")
    pipe.process(meeting.id)
    pipe.process(meeting.id)
    assert len(transcriber.calls) == 1 and len(client.calls) == 2
    pipe.process(meeting.id, force_transcribe=True)
    assert len(transcriber.calls) == 2


def test_pipeline_worker_resumes_pending(db, tmp_path):
    wav = write_wav(tmp_path / "a.wav", tone(1))
    mid = db.create_meeting(Meeting(date="2026-09-30", title="중단된 회의", started_at=1, ended_at=2, source="in_app", audio_path=str(wav), status=MeetingStatus.TRANSCRIBING))
    pipe, _, _ = make_pipeline(db)
    pipe.start()
    deadline = time.time() + 5
    while time.time() < deadline and db.get_meeting(mid).status != MeetingStatus.DONE:
        time.sleep(0.05)
    pipe.stop()
    assert db.get_meeting(mid).status == MeetingStatus.DONE


def test_cleanup_only_removes_own_old_recordings(db, tmp_path):
    pipe, _, _ = make_pipeline(db, settings=Settings(audio_retention_days=30))
    own = write_wav(paths.audio_dir() / "2026-08" / "meeting_20260801_100000_dual.flac".replace(".flac", ".wav"), tone(1))
    user_file = write_wav(tmp_path / "Sound Recordings" / "녹음.wav", tone(1))
    old = time.time() - 40 * 86400
    m_own = db.create_meeting(Meeting(date="2026-08-01", title="a", started_at=old, ended_at=old + 1, source="in_app", audio_path=str(own), status=MeetingStatus.DONE))
    m_user = db.create_meeting(Meeting(date="2026-08-01", title="b", started_at=old, ended_at=old + 1, source="recorder_app", audio_path=str(user_file), status=MeetingStatus.DONE))
    assert pipe.cleanup_old_audio() == 1
    assert not own.exists() and user_file.exists()
    assert db.get_meeting(m_own).audio_path == "" and db.get_meeting(m_user).audio_path == str(user_file)
