"""제외(프라이버시) 규칙과 카테고리 분류."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta

from ..config import CategoryRule, Settings
from .base import WindowInfo

MASKED_TITLE = "[제외됨]"


def is_excluded(win: WindowInfo, settings: Settings) -> bool:
    names = {win.exe_name.lower(), win.app_name.lower()}
    for app in settings.excluded_apps:
        app = app.strip().lower()
        if app and (app in names or any(app in n for n in names if n)):
            return True
    title = win.window_title.lower()
    return any(k.strip() and k.strip().lower() in title for k in settings.excluded_title_keywords)


def mask(win: WindowInfo, settings: Settings) -> WindowInfo:
    """제외 대상이면 창 제목을 가린다(앱 이름과 사용 시간은 남긴다)."""
    if is_excluded(win, settings):
        return replace(win, window_title=MASKED_TITLE)
    return win


def categorize(app_name: str, window_title: str, rules: list[CategoryRule]) -> str:
    for rule in rules:
        target = app_name if rule.field == "app" else window_title
        try:
            if re.search(rule.pattern, target, re.IGNORECASE):
                return rule.category
        except re.error:
            continue
    return ""


# 근무시간 외 수집을 끈 경우에도 출근 전·야근 시간은 기록되도록 여유를 둔다.
BEFORE_WORK_MARGIN = timedelta(minutes=30)
AFTER_WORK_MARGIN = timedelta(hours=3)


def within_tracking_hours(now: datetime, settings: Settings) -> bool:
    if settings.track_outside_work_hours:
        return True
    start = datetime.combine(now.date(), settings.work_start_time()) - BEFORE_WORK_MARGIN
    end = datetime.combine(now.date(), settings.work_end_time()) + AFTER_WORK_MARGIN
    return start <= now < end
