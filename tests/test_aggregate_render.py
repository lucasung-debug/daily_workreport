from datetime import datetime

from workreport.analysis.aggregate import build_summary, clean_title, summarize_day
from workreport.models import (
    ActionItem,
    ActionItemDraft,
    ActivitySession,
    DailyReport,
    Meeting,
    MeetingMinutes,
    ReportItem,
    Segment,
    Transcript,
)
from workreport.report.render import format_duration, minutes_to_markdown, report_to_markdown, report_to_plaintext

DAY = "2026-09-30"


def ts(hhmm):
    return datetime.fromisoformat(f"{DAY}T{hhmm}:00").timestamp()


def sample_sessions():
    return [
        ActivitySession(ts("09:00"), ts("09:50"), "Visual Studio Code", "api.py - backend - Visual Studio Code", category="백엔드"),
        ActivitySession(ts("09:50"), ts("10:00"), "(자리 비움)", "", is_idle=True),
        ActivitySession(ts("10:00"), ts("10:40"), "Microsoft Edge", "PROJ-12 로그인 오류 - Jira 및 페이지 2개 더 - 개인 - Microsoft Edge"),
        ActivitySession(ts("10:40"), ts("10:40") + 10, "메모장", "짧음"),
        ActivitySession(ts("14:00"), ts("15:00"), "Microsoft Teams", "주간회의 | Microsoft Teams"),
    ]


def sample_meeting():
    return Meeting(date=DAY, title="주간회의", started_at=ts("14:00"), ended_at=ts("15:00"), source="in_app", audio_path="")


def test_clean_title():
    assert clean_title("PROJ-12 로그인 오류 - Jira 및 페이지 2개 더 - 개인 - Microsoft Edge", "Microsoft Edge") == "PROJ-12 로그인 오류 - Jira"
    assert clean_title("api.py - backend - Visual Studio Code", "Visual Studio Code") == "api.py - backend"
    assert clean_title("뉴스 - Google Chrome") == "뉴스"


def test_build_summary_totals_and_timeline():
    summary = build_summary(DAY, sample_sessions(), [sample_meeting()])
    assert summary.active_sec == (50 + 40 + 60) * 60 + 10
    assert summary.idle_sec == 600
    assert summary.first_ts == ts("09:00") and summary.last_ts == ts("15:00")
    assert summary.apps[0].app_name == "Microsoft Teams"
    assert "짧음" not in [t.title for t in summary.titles]  # 30초 미만 제외
    assert summary.categories == {"백엔드": 3000}
    labels = {datetime.fromtimestamp(b.start_ts).strftime("%H:%M"): b for b in summary.timeline}
    assert labels["09:00"].label.startswith("Visual Studio Code")
    assert labels["14:00"].is_meeting and labels["14:00"].label == "회의: 주간회의"
    assert "12:00" not in labels  # 활동 없는 구간은 생략


def test_summarize_day_from_db(db):
    for s in sample_sessions():
        db.insert_session(s)
    db.create_meeting(sample_meeting())
    summary = summarize_day(db, DAY)
    assert len(summary.meetings) == 1
    assert summary.active_sec > 0


def test_empty_day():
    summary = build_summary(DAY, [])
    assert summary.timeline == [] and summary.first_ts is None


def report():
    return DailyReport(
        date=DAY,
        summary="로그인 오류 수정과 주간회의",
        accomplishments=[ReportItem(title="PROJ-12 로그인 오류 수정", detail="토큰 만료 처리\n- 테스트 추가", time_spent_min=90, category="백엔드")],
        plans=[ReportItem(title="배포 준비")],
        issues=[],
        memo="QA 일정 확인 필요",
    )


def test_plaintext():
    text = report_to_plaintext(report())
    assert "[일일 업무일지] 2026-09-30 (수)" in text
    assert "1. PROJ-12 로그인 오류 수정 (1시간 30분, 백엔드)" in text
    assert "   - 테스트 추가" in text
    assert "■ 이슈 및 협조 요청\n- 없음" in text
    assert "■ 비고\nQA 일정 확인 필요" in text


def test_markdown_with_appendix():
    meeting = sample_meeting()
    meeting.minutes = MeetingMinutes(title="주간회의", summary="s", attendees=[], discussion=[], decisions=["배포는 금요일"], action_items=[], open_questions=[])
    summary = build_summary(DAY, sample_sessions(), [meeting])
    md = report_to_markdown(report(), summary, author="홍길동", action_items=[(ActionItem(meeting_id=1, task="릴리스 노트", due="2026-10-02", is_mine=True), meeting)])
    assert md.startswith("# 일일 업무일지 — 2026-09-30 (수)")
    assert "작성자: 홍길동 · 근무 09:00 ~ 15:00" in md
    assert "결정: 배포는 금요일" in md
    assert "- [ ] 릴리스 노트 (기한 2026-10-02) — 주간회의" in md
    assert "## 부록 A. 앱별 사용 시간" in md and "## 부록 B. 시간대별 타임라인" in md


def test_minutes_markdown():
    meeting = sample_meeting()
    meeting.transcript = Transcript(segments=[Segment(start=65, end=70, text="배포는 금요일로 하죠", speaker="상대방")])
    meeting.minutes = MeetingMinutes(
        title="주간회의",
        summary="배포 일정 논의",
        attendees=["홍길동", "김철수"],
        discussion=[],
        decisions=["배포는 금요일"],
        action_items=[ActionItemDraft(owner="홍길동", task="릴리스 노트", due="", is_mine=True)],
        open_questions=["QA 인력"],
    )
    md = minutes_to_markdown(meeting)
    assert "- 참석자: 홍길동, 김철수" in md
    assert "| 홍길동 | 릴리스 노트 | - |  |" in md
    assert "`00:01:05` **상대방** 배포는 금요일로 하죠" in md
    assert "(회의록이 아직" in minutes_to_markdown(sample_meeting())


def test_format_duration():
    assert format_duration(45 * 60) == "45분"
    assert format_duration(3600) == "1시간"
    assert format_duration(5400) == "1시간 30분"
