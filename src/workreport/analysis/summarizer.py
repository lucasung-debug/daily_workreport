"""업무일지 초안 생성: 활동 집계 + 회의록 + 액션아이템 + 메모 → Claude → DailyReportDraft.

Claude 를 쓸 수 없으면 fallback_draft() 가 통계만으로 초안을 만든다.
"""

from __future__ import annotations

from datetime import datetime

from ..config import Settings
from ..db import Database, day_bounds
from ..models import DailyReport, DailyReportDraft, ReportItem
from ..report.render import date_label, format_duration, hm
from ..todos import KIND_ACTION, TodoEntry, TodoService, _norm
from .aggregate import DaySummary, summarize_day
from .claude_client import ClaudeService
from .prompts import DAILY_REPORT_SYSTEM

MAX_TITLES = 200
FALLBACK_MIN_SEC = 10 * 60


def build_daily_input(db: Database, day: str, settings: Settings, summary: DaySummary | None = None) -> str:
    summary = summary or summarize_day(db, day, top_titles=MAX_TITLES)
    out: list[str] = [f"# 날짜: {date_label(day)}"]
    if settings.user_name:
        out.append(f"# 작성자: {settings.user_name}")

    out.append("\n## 근무 개요")
    if summary.first_ts:
        out.append(f"- 첫 활동 {hm(summary.first_ts)}, 마지막 활동 {hm(summary.last_ts)}")
    out.append(f"- PC 활동 {format_duration(summary.active_sec)}, 자리 비움 {format_duration(summary.idle_sec)}")

    if summary.apps:
        out.append("\n## 앱별 사용 시간")
        out += [f"- {u.app_name}: {round(u.seconds / 60)}분" for u in summary.apps[:15]]
    if summary.titles:
        out.append("\n## 창 제목별 사용 시간 (분 | 앱 | 제목 | 분류)")
        for t in summary.titles[:MAX_TITLES]:
            out.append(f"- {max(1, round(t.seconds / 60))} | {t.app_name} | {t.title or '(제목 없음)'} | {t.category or '-'}")
    if summary.timeline:
        out.append("\n## 시간대별 주요 작업")
        out += [f"- {hm(b.start_ts)}~{hm(b.end_ts)} {b.label}" for b in summary.timeline]

    out.append("\n## 오늘 회의")
    if not summary.meetings:
        out.append("- 없음")
    for m in summary.meetings:
        out.append(f"### {hm(m.started_at)}~{hm(m.ended_at)} {m.title} ({format_duration(m.duration_sec)})")
        if m.minutes:
            out.append(f"- 요약: {m.minutes.summary}")
            out += [f"- 결정: {d}" for d in m.minutes.decisions]
            out += [f"- 미결: {q}" for q in m.minutes.open_questions]
            for a in m.minutes.action_items:
                owner = "본인" if a.is_mine else (a.owner or "미정")
                out.append(f"- 액션아이템({owner}): {a.task}{f' ~{a.due}' if a.due else ''}")
        else:
            out.append("- (회의록 미작성)")

    todos = TodoService(db)
    done = todos.completed_on(day)
    out.append("\n## 오늘 완료한 할 일")
    out += [f"- {datetime.fromtimestamp(e.done_at):%H:%M} {e.title} — {source_label(e)}" for e in done if e.done_at] or ["- 없음"]
    remaining = todos.open_entries(day)
    out.append("\n## 남은 할 일")
    out += [f"- {'[중요] ' if e.important else ''}{e.title}{f' (기한 {e.due})' if e.due else ''} — {source_label(e)}" for e in remaining] or ["- 없음"]

    notes = db.notes_for(day)
    out.append("\n## 오늘 메모")
    out += [f"- {datetime.fromtimestamp(n.ts):%H:%M} {n.text}" for n in notes] or ["- 없음"]

    prev = db.previous_report(day)
    if prev and prev.plans:
        out.append(f"\n## 전날({prev.date}) 명일 계획")
        out += [f"- {p.title}" for p in prev.plans]

    shots = [s for s in db.screenshots_between(*day_bounds(day)) if s.caption]
    if shots:
        out.append("\n## 화면 캡션 (주기 스크린샷)")
        out += [f"- {datetime.fromtimestamp(s.ts):%H:%M} {s.caption}" for s in shots]

    return "\n".join(out)


def source_label(entry: TodoEntry) -> str:
    return entry.subtitle or "직접 추가"


def generate_draft(service: ClaudeService, db: Database, day: str, settings: Settings) -> DailyReportDraft:
    content = build_daily_input(db, day, settings)
    content += "\n\n위 기록으로 일일 업무일지 초안을 작성하세요."
    return service.structured(DAILY_REPORT_SYSTEM, content, DailyReportDraft)


def fallback_draft(db: Database, day: str, settings: Settings) -> DailyReportDraft:
    """Claude 없이 통계만으로 만든 초안."""
    summary = summarize_day(db, day)
    accomplishments: list[ReportItem] = []
    for m in summary.meetings:
        detail = "\n".join(m.minutes.decisions) if m.minutes else ""
        accomplishments.append(ReportItem(title=f"회의 참석: {m.title}", detail=detail, time_spent_min=round(m.duration_sec / 60), category="회의"))
    if summary.categories:
        for cat, sec in summary.categories.items():
            if sec >= FALLBACK_MIN_SEC:
                titles = [t.title for t in summary.titles if t.category == cat][:3]
                accomplishments.append(ReportItem(title=cat, detail="\n".join(titles), time_spent_min=round(sec / 60), category=cat))
    todos = TodoService(db)
    for e in todos.completed_on(day):
        accomplishments.append(ReportItem(title=e.title, detail=source_label(e) if e.subtitle else "", category="할 일"))
    for t in summary.titles:
        if len(accomplishments) >= 8:
            break
        if t.category or t.seconds < FALLBACK_MIN_SEC or not t.title:
            continue
        accomplishments.append(ReportItem(title=f"{t.title}", detail=t.app_name, time_spent_min=round(t.seconds / 60)))

    plans: list[ReportItem] = []
    seen: set[str] = set()
    for e in todos.open_entries(day):
        if _norm(e.title) in seen:
            continue
        seen.add(_norm(e.title))
        detail = " · ".join(filter(None, [e.subtitle, f"기한 {e.due}" if e.due else ""]))
        plans.append(ReportItem(title=e.title, detail=detail, category="액션아이템" if e.kind == KIND_ACTION else "할 일"))
    issues = [
        ReportItem(title=q, detail=m.title, category="회의 미결")
        for m in summary.meetings
        if m.minutes
        for q in m.minutes.open_questions
    ]
    headline = f"PC 활동 {format_duration(summary.active_sec)}"
    if summary.meetings:
        headline += f", 회의 {len(summary.meetings)}건"
    return DailyReportDraft(summary=f"{headline} (자동 통계 초안)", accomplishments=accomplishments, plans=plans, issues=issues)


def apply_draft(draft: DailyReportDraft, day: str, existing: DailyReport | None = None) -> DailyReport:
    """초안을 업무일지에 반영한다. 메모는 유지하고 상태는 draft 로 되돌린다."""
    report = existing or DailyReport(date=day)
    report.summary = draft.summary
    report.accomplishments = list(draft.accomplishments)
    report.plans = list(draft.plans)
    report.issues = list(draft.issues)
    report.ai_draft = draft
    report.status = "draft"
    return report
