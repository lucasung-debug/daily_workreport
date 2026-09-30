"""업무일지·회의록 → Markdown / 그룹웨어 붙여넣기용 평문."""

from __future__ import annotations

from datetime import date, datetime

from ..analysis.aggregate import DaySummary
from ..models import ActionItem, DailyReport, Meeting, ReportItem, format_hms

WEEKDAYS = "월화수목금토일"


def format_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}시간 {mins}분"
    if hours:
        return f"{hours}시간"
    return f"{mins}분"


def hm(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--:--"


def date_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.isoformat()} ({WEEKDAYS[d.weekday()]})"


def _item_line(idx: int, item: ReportItem, numbered: bool = True) -> list[str]:
    head = f"{idx}. " if numbered else "- "
    extra = []
    if item.time_spent_min:
        extra.append(format_duration(item.time_spent_min * 60))
    if item.category:
        extra.append(item.category)
    suffix = f" ({', '.join(extra)})" if extra else ""
    lines = [f"{head}{item.title}{suffix}"]
    for detail in filter(None, (d.strip() for d in item.detail.splitlines())):
        lines.append(f"   - {detail.lstrip('-• ').strip()}")
    return lines


def _section(items: list[ReportItem], empty: str = "없음") -> list[str]:
    if not items:
        return [f"- {empty}"]
    out: list[str] = []
    for i, item in enumerate(items, 1):
        out.extend(_item_line(i, item))
    return out


def report_to_plaintext(report: DailyReport) -> str:
    lines = [f"[일일 업무일지] {date_label(report.date)}", ""]
    if report.summary:
        lines += [f"요약: {report.summary}", ""]
    lines += ["■ 금일 실적", *_section(report.accomplishments), ""]
    lines += ["■ 명일 계획", *_section(report.plans), ""]
    lines += ["■ 이슈 및 협조 요청", *_section(report.issues)]
    if report.memo.strip():
        lines += ["", "■ 비고", report.memo.strip()]
    return "\n".join(lines).rstrip() + "\n"


def report_to_markdown(
    report: DailyReport,
    summary: DaySummary | None = None,
    author: str = "",
    action_items: list[tuple[ActionItem, Meeting]] | None = None,
) -> str:
    lines = [f"# 일일 업무일지 — {date_label(report.date)}", ""]
    meta = []
    if author:
        meta.append(f"작성자: {author}")
    if summary and summary.first_ts:
        meta.append(f"근무 {hm(summary.first_ts)} ~ {hm(summary.last_ts)} (활동 {format_duration(summary.active_sec)})")
    if meta:
        lines += [" · ".join(meta), ""]
    if report.summary:
        lines += [f"> {report.summary}", ""]
    lines += ["## 1. 금일 실적", *_section(report.accomplishments), ""]
    lines += ["## 2. 명일 계획", *_section(report.plans), ""]
    lines += ["## 3. 이슈 및 협조 요청", *_section(report.issues), ""]
    if report.memo.strip():
        lines += ["## 비고", report.memo.strip(), ""]

    if summary and summary.meetings:
        lines += ["## 회의", ""]
        for m in summary.meetings:
            lines.append(f"- {hm(m.started_at)}~{hm(m.ended_at)} **{m.title}**")
            if m.minutes and m.minutes.decisions:
                lines.append(f"  - 결정: {'; '.join(m.minutes.decisions)}")
        lines.append("")
    if action_items:
        lines += ["## 내 미완료 액션아이템", ""]
        for item, meeting in action_items:
            due = f" (기한 {item.due})" if item.due else ""
            lines.append(f"- [ ] {item.task}{due} — {meeting.title}")
        lines.append("")

    if summary and summary.apps:
        lines += ["## 부록 A. 앱별 사용 시간", "", "| 앱 | 시간 |", "|---|---|"]
        lines += [f"| {u.app_name} | {format_duration(u.seconds)} |" for u in summary.apps[:15]]
        lines.append("")
    if summary and summary.timeline:
        lines += ["## 부록 B. 시간대별 타임라인", "", "| 시간 | 주요 작업 |", "|---|---|"]
        lines += [f"| {hm(b.start_ts)}~{hm(b.end_ts)} | {b.label.replace('|', '/')} |" for b in summary.timeline]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def minutes_to_markdown(meeting: Meeting, action_items: list[ActionItem] | None = None) -> str:
    m = meeting.minutes
    title = (m.title if m and m.title else meeting.title) or "회의"
    lines = [f"# 회의록 — {title}", "", f"- 일시: {meeting.date} {hm(meeting.started_at)}~{hm(meeting.ended_at)} ({format_duration(meeting.duration_sec)})"]
    if m is None:
        lines += ["", "(회의록이 아직 작성되지 않았습니다)"]
    else:
        if m.attendees:
            lines.append(f"- 참석자: {', '.join(m.attendees)}")
        lines += ["", "## 요약", m.summary, ""]
        if m.discussion:
            lines.append("## 논의 내용")
            for topic in m.discussion:
                lines.append(f"### {topic.topic}")
                lines += [f"- {p}" for p in topic.points]
            lines.append("")
        lines += ["## 결정 사항", *([f"- {d}" for d in m.decisions] or ["- 없음"]), ""]
        items = action_items if action_items is not None else None
        lines += ["## 액션 아이템", "", "| 담당 | 할 일 | 기한 | 완료 |", "|---|---|---|---|"]
        if items is not None:
            rows = [(a.owner, a.task, a.due, "✔" if a.done else "") for a in items]
        else:
            rows = [(a.owner, a.task, a.due, "") for a in m.action_items]
        lines += [f"| {o or '-'} | {t} | {d or '-'} | {done} |" for o, t, d, done in rows] or ["| - | 없음 | - | |"]
        lines.append("")
        if m.open_questions:
            lines += ["## 미결 사항", *[f"- {q}" for q in m.open_questions], ""]
    if meeting.transcript and meeting.transcript.segments:
        lines += ["<details><summary>전사문</summary>", ""]
        for seg in meeting.transcript.segments:
            speaker = f"**{seg.speaker}** " if seg.speaker else ""
            lines.append(f"`{format_hms(seg.start)}` {speaker}{seg.text.strip()}  ")
        lines += ["", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"
