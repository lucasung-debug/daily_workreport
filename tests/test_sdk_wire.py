"""실제 anthropic SDK 로 요청을 만들어 HTTP 본문 형태와 응답 파싱을 확인한다(네트워크 없음)."""

import json

import anthropic
import httpx2

from workreport.analysis.claude_client import ClaudeService
from workreport.config import Settings
from workreport.models import DailyReportDraft


def test_sdk_request_body_and_parse():
    seen = {}
    payload = {"summary": "요약", "accomplishments": [{"title": "A", "detail": "", "time_spent_min": 30, "category": ""}], "plans": [], "issues": []}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )

    client = anthropic.Anthropic(api_key="sk-test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    svc = ClaudeService(lambda: Settings(), client_factory=lambda: client, configured=lambda: True)
    out = svc.structured("SYS", "입력", DailyReportDraft)
    assert out.accomplishments[0].time_spent_min == 30

    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
