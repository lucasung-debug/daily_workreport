"""(옵션) 스크린샷 → 한 문장 작업 설명."""

from __future__ import annotations

import base64
from pathlib import Path

from .claude_client import ClaudeService
from .prompts import CAPTION_SYSTEM


def make_caption_fn(service: ClaudeService):
    def caption(image_path: Path, app_name: str, window_title: str) -> str:
        data = base64.standard_b64encode(Path(image_path).read_bytes()).decode("ascii")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": f"앱: {app_name}\n창 제목: {window_title}"},
        ]
        return service.text(CAPTION_SYSTEM, content, max_tokens=2048, effort="low")

    return caption
