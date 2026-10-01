from datetime import datetime

import pytest

from workreport.models import ActionItem, DailyReport, Meeting, ReportItem
from workreport.todos import KIND_ACTION, KIND_MANUAL, KIND_PLAN, TodoService, plan_label

TODAY = "2026-10-01"


class Clock:
    def __init__(self):
        self.t = datetime.fromisoformat(f"{TODAY}T10:00:00").timestamp()

    def __call__(self):
        self.t += 1
        return self.t


def svc(db):
    return TodoService(db, clock=Clock())


def add_meeting(db, title="주간회의", tasks=(("릴리스 노트", True, "2026-10-02"),)):
    start = datetime.fromisoformat("2026-09-30T14:00:00").timestamp()
    mid = db.create_meeting(Meeting(date="2026-09-30", title=title, started_at=start, ended_at=start + 1800, source="import", audio_path=""))
    db.replace_action_items(mid, [ActionItem(meeting_id=mid, task=t, is_mine=mine, due=due) for t, mine, due in tasks])
    return mid


def test_merges_three_sources_and_sorts(db):
    s = svc(db)
    add_meeting(db, tasks=(("릴리스 노트", True, "2026-10-03"), ("남의 일", False, "")))
    db.save_report(DailyReport(date="2026-09-30", plans=[ReportItem(title="배포 준비"), ReportItem(title="")]))
    assert s.sync_plans(TODAY) == 1
    s.add("QA 요청 메일", due=TODAY)
    s.add("문서 정리", important=True)
    s.add("한참 뒤 일", due="2026-12-01")

    entries = s.entries(TODAY)
    assert [e.title for e in entries] == ["QA 요청 메일", "문서 정리", "릴리스 노트", "한참 뒤 일", "배포 준비"]
    kinds = {e.title: e.kind for e in entries}
    assert kinds == {"QA 요청 메일": KIND_MANUAL, "문서 정리": KIND_MANUAL, "릴리스 노트": KIND_ACTION, "한참 뒤 일": KIND_MANUAL, "배포 준비": KIND_PLAN}
    assert next(e for e in entries if e.kind == KIND_PLAN).subtitle == "어제 계획"
    assert next(e for e in entries if e.kind == KIND_ACTION).subtitle == "회의 · 주간회의"
    assert s.open_count(TODAY) == 5


def test_sync_plans_is_idempotent_and_respects_delete(db):
    s = svc(db)
    db.save_report(DailyReport(date="2026-09-30", plans=[ReportItem(title="배포 준비"), ReportItem(title="회고 작성")]))
    assert s.sync_plans(TODAY) == 2
    assert s.sync_plans(TODAY) == 0
    plan = next(e for e in s.entries(TODAY) if e.title == "회고 작성")
    s.delete(plan)
    assert s.sync_plans(TODAY) == 0
    assert [e.title for e in s.entries(TODAY)] == ["배포 준비"]


def test_sync_skips_plan_duplicating_action_item(db):
    s = svc(db)
    add_meeting(db, tasks=(("릴리스 노트 작성", True, ""),))
    db.save_report(DailyReport(date="2026-09-30", plans=[ReportItem(title="릴리스 노트  작성")]))
    assert s.sync_plans(TODAY) == 0
    assert [e.kind for e in s.entries(TODAY)] == [KIND_ACTION]


def test_complete_and_reopen(db):
    s = svc(db)
    mid = add_meeting(db)
    todo = s.add("보고서 쓰기")
    action = next(e for e in s.entries(TODAY) if e.kind == KIND_ACTION)
    s.set_done(todo, True)
    s.set_done(action, True)
    assert db.action_items_for(mid)[0].done  # 회의록 체크와 같은 데이터
    assert s.open_entries(TODAY) == []
    done = s.completed_on(datetime.fromtimestamp(db.get_todo(todo.id).done_at).date().isoformat())
    assert {e.title for e in done} == {"보고서 쓰기", "릴리스 노트"}
    s.set_done(todo, False)
    assert [e.title for e in s.open_entries(TODAY)] == ["보고서 쓰기"]


def test_completed_only_shows_on_that_day(db):
    s = svc(db)
    todo = s.add("어제 끝낸 일")
    s.set_done(todo, True)
    db.update_todo(todo.id, done_at=datetime.fromisoformat("2026-09-30T15:00:00").timestamp())
    assert s.entries(TODAY) == []
    assert [e.title for e in s.completed_on("2026-09-30")] == ["어제 끝낸 일"]


def test_edit_operations_and_action_guard(db):
    s = svc(db)
    add_meeting(db)
    todo = s.add("초안")
    s.rename(todo, "최종안")
    s.set_due(todo, "2026-10-05")
    s.toggle_important(todo)
    stored = db.get_todo(todo.id)
    assert (stored.title, stored.due, stored.important) == ("최종안", "2026-10-05", True)
    action = next(e for e in s.entries(TODAY) if e.kind == KIND_ACTION)
    with pytest.raises(ValueError):
        s.delete(action)
    with pytest.raises(ValueError):
        s.add("   ")


def test_due_state_and_listeners(db):
    s = svc(db)
    calls = []
    s.listeners.append(lambda: calls.append(1))
    e = s.add("x", due="2026-09-30")
    assert e.due_state(TODAY) == "overdue"
    e.due = TODAY
    assert e.due_state(TODAY) == "today"
    e.due = "2026-10-02"
    assert e.due_state(TODAY) == "soon"
    e.due = ""
    assert e.due_state(TODAY) == "none"
    assert calls == [1]
    assert plan_label("2026-09-28", TODAY) == "9/28 계획"
