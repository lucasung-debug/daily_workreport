"""하루 활동 세션 → 앱·제목별 사용 시간, 30분 단위 타임라인."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..db import Database, day_bounds
from ..models import ActivitySession, Meeting

MIN_TITLE_SEC = 30  # 이보다 짧게 스쳐 간 창은 요약 입력에서 뺀다

_BROWSER_SUFFIX = re.compile(
    r"(\s+및 페이지 \d+개 더)?(\s+-\s+(개인|회사|업무|Personal|Work)(\s+\d+)?)?"
    r"\s+[-—]\s+(Google Chrome|Chrome|Microsoft Edge|Mozilla Firefox|Whale|Naver Whale)$"
)


def clean_title(title: str, app_name: str = "") -> str:
    title = title.strip()
    title = _BROWSER_SUFFIX.sub("", title)
    if app_name:
        for sep in (" - ", " — "):
            suffix = f"{sep}{app_name}"
            if title.endswith(suffix) and len(title) > len(suffix):
                title = title[: -len(suffix)]
    return title


@dataclass
class AppUsage:
    app_name: str
    seconds: float


@dataclass
class TitleUsage:
    app_name: str
    title: str
    seconds: float
    category: str = ""


@dataclass
class TimelineBlock:
    start_ts: float
    end_ts: float
    label: str
    active_sec: float
    is_meeting: bool = False


@dataclass
class DaySummary:
    date: str
    first_ts: float | None = None
    last_ts: float | None = None
    active_sec: float = 0.0
    idle_sec: float = 0.0
    apps: list[AppUsage] = field(default_factory=list)
    titles: list[TitleUsage] = field(default_factory=list)
    categories: dict[str, float] = field(default_factory=dict)
    timeline: list[TimelineBlock] = field(default_factory=list)
    meetings: list[Meeting] = field(default_factory=list)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def build_summary(
    day: str,
    sessions: list[ActivitySession],
    meetings: list[Meeting] | None = None,
    top_titles: int = 200,
    bucket_min: int = 30,
) -> DaySummary:
    meetings = meetings or []
    summary = DaySummary(date=day, meetings=meetings)
    active = [s for s in sessions if not s.is_idle and s.duration > 0]
    summary.active_sec = sum(s.duration for s in active)
    summary.idle_sec = sum(s.duration for s in sessions if s.is_idle)
    if active:
        summary.first_ts = min(s.start_ts for s in active)
        summary.last_ts = max(s.end_ts for s in active)

    by_app: dict[str, float] = defaultdict(float)
    by_title: dict[tuple[str, str], list] = {}
    by_cat: dict[str, float] = defaultdict(float)
    for s in active:
        by_app[s.app_name] += s.duration
        title = clean_title(s.window_title, s.app_name)
        entry = by_title.setdefault((s.app_name, title), [0.0, s.category])
        entry[0] += s.duration
        if s.category:
            by_cat[s.category] += s.duration

    summary.apps = sorted((AppUsage(a, sec) for a, sec in by_app.items()), key=lambda u: -u.seconds)
    titles = [TitleUsage(app, title, sec, cat) for (app, title), (sec, cat) in by_title.items() if sec >= MIN_TITLE_SEC]
    summary.titles = sorted(titles, key=lambda u: -u.seconds)[:top_titles]
    summary.categories = dict(sorted(by_cat.items(), key=lambda kv: -kv[1]))
    summary.timeline = _timeline(day, active, meetings, bucket_min)
    return summary


def _timeline(day: str, active: list[ActivitySession], meetings: list[Meeting], bucket_min: int) -> list[TimelineBlock]:
    if not active and not meetings:
        return []
    day_start, day_end = day_bounds(day)
    bucket = bucket_min * 60
    starts = [s.start_ts for s in active] + [m.started_at for m in meetings]
    ends = [s.end_ts for s in active] + [m.ended_at for m in meetings]
    first = max(day_start, min(starts))
    last = min(day_end, max(ends))
    t = day_start + ((first - day_start) // bucket) * bucket
    blocks = []
    while t < last:
        b0, b1 = t, t + bucket
        meeting = max(meetings, key=lambda m: _overlap(b0, b1, m.started_at, m.ended_at), default=None)
        per_label: dict[str, float] = defaultdict(float)
        active_sec = 0.0
        for s in active:
            ov = _overlap(b0, b1, s.start_ts, s.end_ts)
            if ov:
                active_sec += ov
                title = clean_title(s.window_title, s.app_name)
                per_label[f"{s.app_name} · {title}" if title else s.app_name] += ov
        if meeting and _overlap(b0, b1, meeting.started_at, meeting.ended_at) >= bucket / 2:
            blocks.append(TimelineBlock(b0, b1, f"회의: {meeting.title}", active_sec, is_meeting=True))
        elif per_label:
            label = max(per_label.items(), key=lambda kv: kv[1])[0]
            blocks.append(TimelineBlock(b0, b1, label, active_sec))
        t = b1
    return blocks


def summarize_day(db: Database, day: str, top_titles: int = 200) -> DaySummary:
    return build_summary(day, db.sessions_for_date(day), db.meetings_for(day), top_titles=top_titles)
