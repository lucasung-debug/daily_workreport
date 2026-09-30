import time
from datetime import datetime

from audio_util import tone, write_wav
from fakes import FakeClient, parsed
from test_meeting import FakeBackend, minutes
from workreport.collector.base import WindowInfo
from workreport.collector.fake import FakeProbe
from workreport.config import Settings
from workreport.models import ActivitySession, DailyReport, DailyReportDraft, MeetingStatus, ReportItem
from workreport.scheduler import DailyScheduler
from workreport.services import Services, SettingsStore
from workreport.meeting.stt.fake import FakeTranscriber

DAY = "2026-09-30"


def ts(hhmm):
    return datetime.fromisoformat(f"{DAY}T{hhmm}:00").timestamp()


def make_services(db, tmp_path, configured=True, client=None):
    store = SettingsStore(tmp_path / "config.json")
    client = client or FakeClient(default=parsed(DailyReportDraft(summary="AI 요약", accomplishments=[ReportItem(title="AI 항목")], plans=[], issues=[])))
    svc = Services(
        store,
        db,
        probe=FakeProbe(WindowInfo("Code", "a.py", "Code.exe")),
        transcriber_factory=lambda s: FakeTranscriber(),
        claude_client_factory=lambda: client,
        claude_configured=lambda: configured,
        recorder_backend=FakeBackend(),
    )
    return svc, store, client


def test_settings_store_persists(tmp_path):
    store = SettingsStore(tmp_path / "c.json")
    seen = []
    store.listeners.append(seen.append)
    new = store.get().model_copy(update={"user_name": "홍길동"})
    store.update(new)
    assert SettingsStore(tmp_path / "c.json").get().user_name == "홍길동"
    assert seen == [new]


def test_generate_report_with_and_without_claude(db, tmp_path):
    db.insert_session(ActivitySession(ts("09:00"), ts("10:00"), "Code", "a.py - proj"))
    db.save_report(DailyReport(date=DAY, memo="메모 유지"))
    svc, _, client = make_services(db, tmp_path)
    report = svc.generate_report_draft(DAY)
    assert report.accomplishments[0].title == "AI 항목" and report.memo == "메모 유지"
    assert db.get_report(DAY).summary == "AI 요약"

    svc2, _, client2 = make_services(db, tmp_path, configured=False)
    report2 = svc2.generate_report_draft(DAY)
    assert "자동 통계 초안" in report2.summary and client2.calls == []


def test_recording_roundtrip_creates_meeting(db, tmp_path):
    svc, _, _ = make_services(db, tmp_path, client=FakeClient(default=parsed(minutes())))
    svc.start_recording("주간회의")
    time.sleep(0.4)
    meeting = svc.stop_recording()
    assert meeting.source == "in_app" and meeting.title == "주간회의"
    done = svc.pipeline.process(meeting.id)
    assert done.status == MeetingStatus.DONE
    assert {s.speaker for s in done.transcript.segments} == {"나", "상대방"}


def test_pause_and_lifecycle(db, tmp_path):
    svc, _, _ = make_services(db, tmp_path)
    svc.set_paused(True)
    assert svc.paused and svc.screenshots.paused
    svc.set_paused(False)
    svc.start()
    svc.stop()


def test_import_audio(db, tmp_path):
    svc, _, _ = make_services(db, tmp_path)
    meeting = svc.import_audio(write_wav(tmp_path / "고객 미팅.wav", tone(1)))
    assert meeting.source == "import" and meeting.title == "고객 미팅"


def sched(db, settings=None):
    calls = {"draft": [], "day": [], "clean": 0}
    s = settings or Settings(work_end="18:00", draft_minutes_before_end=10)

    def clean():
        calls["clean"] += 1

    return DailyScheduler(lambda: s, db, calls["draft"].append, calls["day"].append, clean), calls


def test_scheduler_auto_draft_once(db):
    db.insert_session(ActivitySession(ts("09:00"), ts("10:00"), "Code", "a.py"))
    scheduler, calls = sched(db)
    scheduler.tick(datetime(2026, 9, 30, 17, 0))
    assert calls["draft"] == [] and calls["clean"] == 1
    scheduler.tick(datetime(2026, 9, 30, 17, 50))
    scheduler.tick(datetime(2026, 9, 30, 17, 55))
    assert calls["draft"] == [DAY]
    scheduler.tick(datetime(2026, 10, 1, 0, 0, 30))
    assert calls["day"] == ["2026-10-01"] and calls["clean"] == 2


def test_scheduler_skips_when_user_wrote_report_or_no_activity(db):
    scheduler, calls = sched(db)
    scheduler.tick(datetime(2026, 9, 30, 18, 0))
    assert calls["draft"] == []  # 활동 없음
    db.insert_session(ActivitySession(ts("09:00"), ts("10:00"), "Code", "a.py"))
    db.save_report(DailyReport(date=DAY, accomplishments=[ReportItem(title="직접 작성")]))
    scheduler2, calls2 = sched(db)
    scheduler2.tick(datetime(2026, 9, 30, 18, 0))
    assert calls2["draft"] == []
    scheduler3, calls3 = sched(db, Settings(auto_draft_enabled=False))
    scheduler3.tick(datetime(2026, 9, 30, 18, 0))
    assert calls3["draft"] == []
