"""Claude API 공통 호출.

- 구조화 출력: client.beta.messages.parse(output_format=<pydantic 모델>)
- 거절(refusal) 시 서버 측 대체 모델로 재시도: fallbacks="default" (설정으로 끌 수 있음)
- SDK 예외를 사용자에게 보여줄 한국어 메시지(ClaudeError)로 바꾼다.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, TypeVar

import anthropic
from pydantic import BaseModel

from .. import credentials
from ..config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeError(Exception):
    """사용자에게 그대로 보여줄 수 있는 오류."""


class ClaudeRefusal(ClaudeError):
    pass


def has_credentials() -> bool:
    return bool(
        credentials.get_secret(credentials.ANTHROPIC_API_KEY)
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def make_client() -> anthropic.Anthropic:
    key = credentials.get_secret(credentials.ANTHROPIC_API_KEY)
    # 키가 없으면 환경 변수(ANTHROPIC_API_KEY 등)를 SDK 가 직접 찾는다
    return anthropic.Anthropic(api_key=key, max_retries=3) if key else anthropic.Anthropic(max_retries=3)


class ClaudeService:
    def __init__(
        self,
        settings: Callable[[], Settings],
        client_factory: Callable[[], Any] = make_client,
        configured: Callable[[], bool] = has_credentials,
    ):
        self._settings = settings
        self._client_factory = client_factory
        self._configured = configured

    def available(self) -> bool:
        return self._configured()

    def _common(self, system: str, content: Any, max_tokens: int, effort: str | None) -> dict[str, Any]:
        s = self._settings()
        kwargs: dict[str, Any] = {
            "model": s.claude_model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
        }
        if not s.claude_model.startswith("claude-haiku"):  # Haiku 4.5 는 effort 미지원
            kwargs["output_config"] = {"effort": effort or s.claude_effort}
        if s.claude_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _call(self, method: str, kwargs: dict[str, Any]):
        client = self._client_factory()
        try:
            return getattr(client.beta.messages, method)(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise ClaudeError("Claude API 키가 올바르지 않습니다. 설정에서 키를 확인하세요.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ClaudeError("이 API 키로는 요청한 모델·기능을 사용할 수 없습니다.") from exc
        except anthropic.NotFoundError as exc:
            raise ClaudeError(f"모델을 찾을 수 없습니다: {kwargs['model']}") from exc
        except anthropic.RateLimitError as exc:
            raise ClaudeError("Claude API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from exc
        except anthropic.BadRequestError as exc:
            raise ClaudeError(f"Claude API 요청 오류: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise ClaudeError(f"Claude API 서버 오류({exc.status_code}). 잠시 후 다시 시도하세요.") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeError("Claude API 에 연결하지 못했습니다. 네트워크·사내 프록시 설정을 확인하세요.") from exc
        except anthropic.AnthropicError as exc:
            raise ClaudeError(f"Claude API 오류: {exc}") from exc

    @staticmethod
    def _check_stop(resp) -> None:
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ClaudeRefusal(f"Claude 가 이 요청을 처리하지 않았습니다{f' ({category})' if category else ''}.")
        if resp.stop_reason == "max_tokens":
            raise ClaudeError("응답이 너무 길어 잘렸습니다. 입력을 줄이거나 다시 시도하세요.")

    def structured(self, system: str, content: Any, output_type: type[T], max_tokens: int = 16000, effort: str | None = None) -> T:
        kwargs = self._common(system, content, max_tokens, effort)
        kwargs["output_format"] = output_type
        try:
            resp = self._call("parse", kwargs)
        except ClaudeError:
            raise
        except Exception as exc:  # 응답 JSON 검증 실패 등
            raise ClaudeError(f"Claude 응답을 해석하지 못했습니다: {exc}") from exc
        self._check_stop(resp)
        parsed = resp.parsed_output
        if parsed is None:
            raise ClaudeError("Claude 응답에 결과가 없습니다.")
        log.info("Claude 사용량: 입력 %s, 출력 %s, 캐시 읽기 %s", resp.usage.input_tokens, resp.usage.output_tokens, getattr(resp.usage, "cache_read_input_tokens", 0))
        return parsed

    def text(self, system: str, content: Any, max_tokens: int = 2048, effort: str | None = None) -> str:
        resp = self._call("create", self._common(system, content, max_tokens, effort))
        self._check_stop(resp)
        return "".join(b.text for b in resp.content if b.type == "text").strip()
