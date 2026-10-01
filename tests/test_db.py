from datetime import datetime

from workreport.db import Database, day_bounds, local_date
from workreport.models import (
    ActionItem,
    ActivitySession,
    DailyReport,
    DailyReportDraft,
    Meeting,
    MeetingMinutes,
    MeetingStatus,
    ReportItem,
    Segment,
    Transcript,
)


def ts(day: str, hhmm: str) -> float:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").timestamp()


def test_migration_sets_version(db):
    assert db.schema_version == 2


def test_reopen_keeps_data(tmp_path):
    path = tmp_path / "x.db"
    first = Database(path)
    first.add_note("메모", ts("2026-09-30", "10:00"))
    first.close()
    second = Database(path)
    assert [n.text for n in second.notes_for("2026-09-30")] == ["메모"]
    assert second.schema_version == 2
    second.close()


def test_sessions_are_clipped_to_day(db):
    day = "2026-09-30"
    db.insert_session(ActivitySession(ts("2026-09-29", "23:50"), ts(day, "00:10"), "a.exe", "야간"))
    sid = db.insert_session(ActivitySession(ts(day, "09:00"), ts(day, "09:05"), "code.exe", "main.py"))
    db.update_session_end(sid, ts(day, "09:30"))
    sessions = db.sessions_for_date(day)
    assert len(sessions) == 2
    assert sessions[0].start_ts == day_bounds(day)[0]
    assert sessions[1].duration == 30 * 60


def test_report_roundtrip_and_upsert(db):
    draft = DailyReportDraft(summary="요약", accomplishments=[ReportItem(title="A")], plans=[], issues=[])
    report = DailyReport(date="2026-09-30", summary="요약", accomplishments=[ReportItem(title="A", time_spent_min=30)], ai_draft=draft)
    db.save_report(report)
    report.status = "final"
    report.plans = [ReportItem(title="B")]
    db.save_report(report)
    loaded = db.get_report("2026-09-30")
    assert loaded.status == "final"
    assert loaded.accomplishments[0].time_spent_min == 30
    assert loaded.plans[0].title == "B"
    assert loaded.ai_draft.summary == "요약"
    assert db.report_dates() == {"2026-09-30": "final"}
    db.save_report(DailyReport(date="2026-10-01"))
    assert db.previous_report("2026-10-01").date == "2026-09-30"


def test_meeting_lifecycle_and_action_items(db):
    start = ts("2026-09-30", "14:00")
    m = Meeting(date=local_date(start), title="주간회의", started_at=start, ended_at=start + 1800, source="import", audio_path="a.m4a")
    mid = db.create_meeting(m)
    assert [x.id for x in db.pending_meetings()] == [mid]

    transcript = Transcript(engine="fake", segments=[Segment(start=0, end=2, text="안녕하세요", speaker="나")])
    minutes = MeetingMinutes(title="주간회의", summary="s", attendees=[], discussion=[], decisions=["결정"], action_items=[], open_questions=[])
    db.update_meeting(mid, status=MeetingStatus.DONE, transcript=transcript, minutes=minutes, progress=1.0)
    loaded = db.get_meeting(mid)
    assert loaded.status == MeetingStatus.DONE
    assert loaded.transcript.segments[0].speaker == "나"
    assert loaded.minutes.decisions == ["결정"]
    assert db.pending_meetings() == []
    assert loaded.duration_sec == 1800

    db.replace_action_items(mid, [ActionItem(meeting_id=mid, task="보고서", is_mine=True, due="2026-10-02"), ActionItem(meeting_id=mid, task="남의 일")])
    mine = db.open_my_action_items()
    assert [(a.task, mt.title) for a, mt in mine] == [("보고서", "주간회의")]
    db.set_action_item_done(mine[0][0].id, True)
    assert db.open_my_action_items() == []

    db.delete_meeting(mid)
    assert db.action_items_for(mid) == []


def test_processed_files(db):
    assert not db.is_processed("C:/a.m4a")
    db.mark_processed("C:/a.m4a", 10, 1.0, None)
    assert db.is_processed("C:/a.m4a")


def test_upgrade_v1_database_keeps_data(tmp_path):
    import sqlite3

    from workreport import db as dbmod

    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript(dbmod._MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO meetings(date, title, started_at, ended_at, source, created_at, updated_at) VALUES ('2026-09-30','회의',1,2,'import',1,1)")
    conn.execute("INSERT INTO action_items(meeting_id, task, is_mine, done) VALUES (1, '보고서', 1, 0)")
    conn.commit()
    conn.close()

    upgraded = Database(path)
    assert upgraded.schema_version == 2
    items = upgraded.action_items_for(1)
    assert [(i.task, i.done, i.done_at) for i in items] == [("보고서", False, None)]
    upgraded.set_action_item_done(items[0].id, True)
    assert upgraded.action_items_for(1)[0].done_at is not None
    upgraded.close()


def test_action_item_done_state_survives_rewrite(db):
    mid = db.create_meeting(Meeting(date="2026-09-30", title="회의", started_at=1, ended_at=2, source="import", audio_path=""))
    db.replace_action_items(mid, [ActionItem(meeting_id=mid, task="A", is_mine=True), ActionItem(meeting_id=mid, task="B", is_mine=True)])
    a = db.action_items_for(mid)[0]
    db.set_action_item_done(a.id, True)
    done_at = db.action_items_for(mid)[0].done_at

    # 회의록 재작성: 같은 할 일의 완료 상태와 완료 시각을 이어받는다
    db.replace_action_items(mid, [ActionItem(meeting_id=mid, task="A", is_mine=True), ActionItem(meeting_id=mid, task="C", is_mine=True)], preserve_done=True)
    rows = {i.task: i for i in db.action_items_for(mid)}
    assert rows["A"].done and rows["A"].done_at == done_at and not rows["C"].done

    # 사용자가 회의록에서 체크를 해제하면 그대로 반영한다
    db.replace_action_items(mid, [ActionItem(meeting_id=mid, task="A", is_mine=True, done=False)])
    assert not db.action_items_for(mid)[0].done and db.action_items_for(mid)[0].done_at is None
    assert db.action_items_done_between(0, 2e10) == []


def test_todo_crud(db):
    from workreport.models import Todo

    tid = db.add_todo(Todo(title="보고서 쓰기", due="2026-10-01"))
    db.add_todo(Todo(title="어제 계획", source="plan", source_ref="2026-09-30#0"))
    assert [t.title for t in db.open_todos()] == ["보고서 쓰기", "어제 계획"]
    db.update_todo(tid, done=True, done_at=100.0, important=True)
    assert db.get_todo(tid).done and db.get_todo(tid).important
    assert [t.title for t in db.todos_done_between(0, 200)] == ["보고서 쓰기"]
    assert db.todo_by_ref("2026-09-30#0").source == "plan"
    db.update_todo(db.todo_by_ref("2026-09-30#0").id, deleted=True)
    assert db.open_todos() == []
    assert db.todo_by_ref("2026-09-30#0").deleted
