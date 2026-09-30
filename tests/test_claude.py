from datetime import datetime

import anthropic
import httpx2
import pytest

from fakes import FakeClient, parsed, text_response
from workreport.analysis.captioner import make_caption_fn
from workreport.analysis.claude_client import FALLBACK_BETA, ClaudeError, ClaudeRefusal, ClaudeService, has_credentials
from workreport.analysis.summarizer import apply_draft, build_daily_input, fallback_draft, generate_draft
from workreport.config import Settings
from workreport.models import (
    ActionItem,
    ActionItemDraft,
    ActivitySession,
    DailyReport,
    DailyReportDraft,
    Meeting,
    MeetingMinutes,
    ReportItem,
)

DAY = "2026-09-30"


def ts(hhmm):
    return datetime.fromisoformat(f"{DAY}T{hhmm}:00").timestamp()


def service(client, settings=None):
    s = settings or Settings()
    return ClaudeService(lambda: s, client_factory=lambda: client, configured=lambda: True)


def draft():
    return DailyReportDraft(summary="요약", accomplishments=[ReportItem(title="A")], plans=[], issues=[])


def status_error(cls, code):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("err", response=httpx2.Response(code, request=req), body=None)


def test_structured_request_shape():
    client = FakeClient(parsed(draft()))
    out = service(client).structured("SYS", "hello", DailyReportDraft)
    assert out.summary == "요약"
    method, kw = client.calls[0]
    assert method == "parse"
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_format"] is DailyReportDraft
    assert kw["output_config"] == {"effort": "medium"}
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"] == [{"role": "user", "content": "hello"}]


def test_fallbacks_can_be_disabled_and_haiku_has_no_effort():
    client = FakeClient(parsed(draft()))
    service(client, Settings(claude_fallbacks=False, claude_model="claude-haiku-4-5")).structured("S", "x", DailyReportDraft)
    kw = client.calls[0][1]
    assert "fallbacks" not in kw and "betas" not in kw and "output_config" not in kw


def test_refusal_and_truncation():
    with pytest.raises(ClaudeRefusal):
        service(FakeClient(parsed(None, stop_reason="refusal"))).structured("S", "x", DailyReportDraft)
    with pytest.raises(ClaudeError, match="잘렸"):
        service(FakeClient(parsed(None, stop_reason="max_tokens"))).structured("S", "x", DailyReportDraft)
    with pytest.raises(ClaudeError, match="결과가 없"):
        service(FakeClient(parsed(None))).structured("S", "x", DailyReportDraft)


@pytest.mark.parametrize(
    "exc, pattern",
    [
        (status_error(anthropic.AuthenticationError, 401), "키가 올바르지"),
        (status_error(anthropic.RateLimitError, 429), "한도"),
        (status_error(anthropic.NotFoundError, 404), "모델을 찾을 수"),
        (status_error(anthropic.InternalServerError, 500), "서버 오류\\(500\\)"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "연결하지 못했"),
        (ValueError("bad json"), "해석하지 못했"),
    ],
)
def test_errors_are_translated(exc, pattern):
    with pytest.raises(ClaudeError, match=pattern):
        service(FakeClient(exc)).structured("S", "x", DailyReportDraft)


def test_text_call_and_caption(tmp_path):
    client = FakeClient(text_response("엑셀로 예산안 작성"))
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8jpeg")
    caption = make_caption_fn(service(client))(img, "Excel", "예산.xlsx")
    assert caption == "엑셀로 예산안 작성"
    method, kw = client.calls[0]
    assert method == "create"
    assert kw["output_config"] == {"effort": "low"}
    content = kw["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"


def test_has_credentials_env(monkeypatch):
    assert not has_credentials()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    assert has_credentials()


def seed(db):
    db.insert_session(ActivitySession(ts("09:00"), ts("10:30"), "Visual Studio Code", "auth.py - backend", category="백엔드"))
    db.insert_session(ActivitySession(ts("10:30"), ts("11:00"), "Microsoft Edge", "PROJ-12 로그인 오류 - Jira"))
    m = Meeting(date=DAY, title="주간회의", started_at=ts("14:00"), ended_at=ts("15:00"), source="in_app", audio_path="")
    m.minutes = MeetingMinutes(
        title="주간회의",
        summary="배포 일정 논의",
        attendees=[],
        discussion=[],
        decisions=["배포는 금요일"],
        action_items=[ActionItemDraft(owner="홍길동", task="릴리스 노트 작성", due="2026-10-02", is_mine=True)],
        open_questions=["QA 인력 확보"],
    )
    mid = db.create_meeting(m)
    db.replace_action_items(mid, [ActionItem(meeting_id=mid, task="릴리스 노트 작성", due="2026-10-02", is_mine=True)])
    db.add_note("오후에 QA팀 문의", ts("16:00"))
    db.save_report(DailyReport(date="2026-09-29", plans=[ReportItem(title="로그인 오류 분석")]))


def test_daily_input_contains_all_sources(db):
    seed(db)
    text = build_daily_input(db, DAY, Settings(user_name="홍길동"))
    assert "# 날짜: 2026-09-30 (수)" in text and "# 작성자: 홍길동" in text
    assert "90 | Visual Studio Code | auth.py - backend | 백엔드" in text
    assert "### 14:00~15:00 주간회의 (1시간)" in text
    assert "- 결정: 배포는 금요일" in text
    assert "액션아이템(본인): 릴리스 노트 작성 ~2026-10-02" in text
    assert "## 본인의 미완료 액션아이템\n- 릴리스 노트 작성 (기한 2026-10-02) — 주간회의 (2026-09-30)" in text
    assert "16:00 오후에 QA팀 문의" in text
    assert "## 전날(2026-09-29) 명일 계획\n- 로그인 오류 분석" in text


def test_generate_draft_sends_input(db):
    seed(db)
    client = FakeClient(parsed(draft()))
    result = generate_draft(service(client), db, DAY, Settings())
    assert result.summary == "요약"
    assert "릴리스 노트 작성" in client.calls[0][1]["messages"][0]["content"]


def test_fallback_draft(db):
    seed(db)
    d = fallback_draft(db, DAY, Settings())
    titles = [a.title for a in d.accomplishments]
    assert titles[0] == "회의 참석: 주간회의"
    assert "백엔드" in titles
    assert "PROJ-12 로그인 오류 - Jira" in titles
    assert [p.title for p in d.plans] == ["릴리스 노트 작성"]
    assert [i.title for i in d.issues] == ["QA 인력 확보"]


def test_apply_draft_keeps_memo():
    existing = DailyReport(date=DAY, memo="메모", status="final")
    report = apply_draft(draft(), DAY, existing)
    assert report.memo == "메모" and report.status == "draft"
    assert report.accomplishments[0].title == "A" and report.ai_draft.summary == "요약"
