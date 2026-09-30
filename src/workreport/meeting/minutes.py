"""전사문 → Claude → 회의록(MeetingMinutes)."""

from __future__ import annotations

from ..analysis.claude_client import ClaudeService
from ..analysis.prompts import MEETING_MINUTES_SYSTEM
from ..config import Settings
from ..models import ActionItem, Meeting, MeetingMinutes
from ..report.render import date_label, format_duration, hm

SELF_OWNERS = {"나", "본인", "기록자"}


def build_minutes_input(meeting: Meeting, settings: Settings) -> str:
    assert meeting.transcript is not None
    lines = [
        f"# 회의 날짜: {date_label(meeting.date)} {hm(meeting.started_at)}~{hm(meeting.ended_at)} ({format_duration(meeting.duration_sec)})",
        f"# 제목 후보: {meeting.title or '(없음)'}",
        f"# 기록자 이름: {settings.user_name or '(미설정)'}",
        "",
        "## 전사문",
        meeting.transcript.to_text(),
        "",
        "위 전사문으로 회의록을 작성하세요.",
    ]
    return "\n".join(lines)


def generate_minutes(service: ClaudeService, meeting: Meeting, settings: Settings) -> MeetingMinutes:
    return service.structured(MEETING_MINUTES_SYSTEM, build_minutes_input(meeting, settings), MeetingMinutes)


def action_items_from(minutes: MeetingMinutes, meeting_id: int, user_name: str = "") -> list[ActionItem]:
    items = []
    for a in minutes.action_items:
        owner = a.owner.strip()
        mine = a.is_mine or owner in SELF_OWNERS or bool(user_name and owner and (user_name in owner or owner in user_name))
        items.append(ActionItem(meeting_id=meeting_id, owner=owner, task=a.task.strip(), due=a.due.strip(), is_mine=mine))
    return items
