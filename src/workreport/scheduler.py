"""하루 일정: 퇴근 N분 전 업무일지 초안 자동 생성, 날짜 전환, 하루 한 번 정리 작업.

Qt 타이머가 30초마다 tick() 을 부른다. 로직 자체는 Qt 에 의존하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .config import Settings
from .db import Database


class DailyScheduler:
    def __init__(
        self,
        settings: Callable[[], Settings],
        db: Database,
        on_auto_draft: Callable[[str], None],
        on_day_change: Callable[[str], None] | None = None,
        on_daily_cleanup: Callable[[], None] | None = None,
    ):
        self._settings = settings
        self.db = db
        self.on_auto_draft = on_auto_draft
        self.on_day_change = on_day_change
        self.on_daily_cleanup = on_daily_cleanup
        self.last_day: str | None = None
        self.last_cleanup_day: str | None = None
        self.last_auto_draft_day: str | None = None

    def draft_time(self, now: datetime) -> datetime:
        s = self._settings()
        return datetime.combine(now.date(), s.work_end_time()) - timedelta(minutes=s.draft_minutes_before_end)

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        day = now.date().isoformat()
        if self.last_day != day:
            if self.last_day and self.on_day_change:
                self.on_day_change(day)
            self.last_day = day
        if self.last_cleanup_day != day:
            self.last_cleanup_day = day
            if self.on_daily_cleanup:
                self.on_daily_cleanup()

        if not self._settings().auto_draft_enabled or self.last_auto_draft_day == day or now < self.draft_time(now):
            return
        self.last_auto_draft_day = day
        if self.should_auto_draft(day):
            self.on_auto_draft(day)

    def should_auto_draft(self, day: str) -> bool:
        report = self.db.get_report(day)
        if report and (report.status == "final" or report.accomplishments or report.plans or report.issues):
            return False  # 사용자가 이미 작성·수정한 일지는 덮어쓰지 않는다
        return bool(self.db.sessions_for_date(day) or self.db.meetings_for(day))
