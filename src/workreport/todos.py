"""오늘 할 일: 직접 추가한 할 일 + 회의 액션아이템(내 일) + 전날 '명일 계획'을 한 목록으로 다룬다.

- 직접 추가·전날 계획은 todos 테이블, 회의 액션아이템은 action_items 테이블에 있다.
- 액션아이템을 완료하면 회의록의 체크 상태도 함께 바뀐다(같은 행을 쓴다).
- 미완료 항목은 날짜와 상관없이 계속 남고, 완료 항목은 완료한 날에만 '완료'로 보인다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from .db import Database, day_bounds
from .models import ActionItem, Meeting, Todo

KIND_MANUAL, KIND_PLAN, KIND_ACTION = "manual", "plan", "action"


@dataclass
class TodoEntry:
    kind: str  # manual | plan | action
    id: int
    title: str
    subtitle: str = ""
    due: str = ""
    important: bool = False
    done: bool = False
    done_at: float | None = None
    created_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def editable(self) -> bool:
        """제목·기한·중요·삭제를 이 목록에서 바꿀 수 있는지(액션아이템은 회의록에서 관리)."""
        return self.kind != KIND_ACTION

    def due_state(self, today: str) -> str:
        """overdue | today | soon | later | none"""
        if not self.due:
            return "none"
        if self.due < today:
            return "overdue"
        if self.due == today:
            return "today"
        if self.due <= (date.fromisoformat(today) + timedelta(days=1)).isoformat():
            return "soon"
        return "later"


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def plan_label(report_date: str, today: str) -> str:
    try:
        prev = date.fromisoformat(report_date)
        if prev == date.fromisoformat(today) - timedelta(days=1):
            return "어제 계획"
        return f"{prev.month}/{prev.day} 계획"
    except ValueError:
        return "지난 계획"


class TodoService:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db = db
        self._clock = clock
        self.listeners: list[Callable[[], None]] = []

    def notify(self) -> None:
        """목록이 바뀌었음을 알린다(회의록 저장처럼 다른 경로로 바뀐 경우에도 부른다)."""
        for listener in list(self.listeners):
            listener()

    # ------------------------------------------------------------ 변환
    def _from_todo(self, t: Todo, today: str) -> TodoEntry:
        subtitle = plan_label(t.source_ref.split("#")[0], today) if t.source == "plan" else ""
        return TodoEntry(
            kind=KIND_PLAN if t.source == "plan" else KIND_MANUAL,
            id=t.id,
            title=t.title,
            subtitle=subtitle,
            due=t.due,
            important=t.important,
            done=t.done,
            done_at=t.done_at,
            created_at=t.created_at,
        )

    @staticmethod
    def _from_action(a: ActionItem, m: Meeting) -> TodoEntry:
        return TodoEntry(
            kind=KIND_ACTION,
            id=a.id,
            title=a.task,
            subtitle=f"회의 · {m.title}",
            due=a.due,
            done=a.done,
            done_at=a.done_at,
            created_at=m.started_at,
        )

    # ------------------------------------------------------------ 조회
    def open_entries(self, today: str | None = None) -> list[TodoEntry]:
        today = today or date.today().isoformat()
        entries = [self._from_todo(t, today) for t in self.db.open_todos()]
        entries += [self._from_action(a, m) for a, m in self.db.open_my_action_items()]

        def key(e: TodoEntry):
            urgent = 0 if e.due and e.due <= today else 1
            return (urgent, 0 if e.important else 1, e.due or "9999-99-99", e.created_at, e.id)

        return sorted(entries, key=key)

    def completed_on(self, day: str) -> list[TodoEntry]:
        start, end = day_bounds(day)
        entries = [self._from_todo(t, day) for t in self.db.todos_done_between(start, end)]
        entries += [self._from_action(a, m) for a, m in self.db.action_items_done_between(start, end)]
        return sorted(entries, key=lambda e: e.done_at or 0)

    def entries(self, day: str | None = None) -> list[TodoEntry]:
        """오늘 볼 목록: 미완료 전부 + 그날 완료한 것."""
        day = day or date.today().isoformat()
        return self.open_entries(day) + self.completed_on(day)

    def open_count(self, day: str | None = None) -> int:
        return len(self.open_entries(day))

    # ------------------------------------------------------------ 가져오기
    def sync_plans(self, today: str | None = None) -> int:
        """가장 최근 업무일지의 '명일 계획'을 할 일로 가져온다. 여러 번 불러도 한 번만 들어간다."""
        today = today or date.today().isoformat()
        prev = self.db.previous_report(today)
        if not prev or not prev.plans:
            return 0
        existing = {_norm(e.title) for e in self.open_entries(today)}
        added = 0
        for index, plan in enumerate(prev.plans):
            title = plan.title.strip()
            ref = f"{prev.date}#{index}"
            if not title or self.db.todo_by_ref(ref) is not None:
                continue
            if _norm(title) in existing:  # 이미 같은 할 일(예: 같은 액션아이템)이 있으면 건너뛴다
                self.db.add_todo(Todo(title=title, source="plan", source_ref=ref, deleted=True, created_at=self._clock()))
                continue
            self.db.add_todo(Todo(title=title, source="plan", source_ref=ref, created_at=self._clock()))
            existing.add(_norm(title))
            added += 1
        if added:
            self.notify()
        return added

    # ------------------------------------------------------------ 변경
    def add(self, title: str, due: str = "", important: bool = False) -> TodoEntry:
        title = title.strip()
        if not title:
            raise ValueError("할 일 제목이 비어 있어요.")
        todo = Todo(title=title, due=due, important=important, created_at=self._clock())
        self.db.add_todo(todo)
        self.notify()
        return self._from_todo(todo, date.today().isoformat())

    def set_done(self, entry: TodoEntry, done: bool) -> None:
        if entry.kind == KIND_ACTION:
            self.db.set_action_item_done(entry.id, done)
        else:
            self.db.update_todo(entry.id, done=done, done_at=self._clock() if done else None)
        entry.done = done
        self.notify()

    def _require_editable(self, entry: TodoEntry) -> None:
        if not entry.editable:
            raise ValueError("회의 액션아이템은 회의록 화면에서 고칠 수 있어요.")

    def rename(self, entry: TodoEntry, title: str) -> None:
        self._require_editable(entry)
        if title.strip() and title.strip() != entry.title:
            self.db.update_todo(entry.id, title=title.strip())
            entry.title = title.strip()
            self.notify()

    def set_due(self, entry: TodoEntry, due: str) -> None:
        self._require_editable(entry)
        self.db.update_todo(entry.id, due=due)
        entry.due = due
        self.notify()

    def toggle_important(self, entry: TodoEntry) -> None:
        self._require_editable(entry)
        entry.important = not entry.important
        self.db.update_todo(entry.id, important=entry.important)
        self.notify()

    def delete(self, entry: TodoEntry) -> None:
        self._require_editable(entry)
        self.db.update_todo(entry.id, deleted=True)  # 소프트 삭제: 지운 '어제 계획'이 다시 들어오지 않게
        self.notify()
