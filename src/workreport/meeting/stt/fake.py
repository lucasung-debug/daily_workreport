"""테스트·데모용 가짜 STT."""

from __future__ import annotations

from pathlib import Path

from ...models import Segment, Transcript
from .base import ProgressCb, STTError


class FakeTranscriber:
    name = "fake"

    def __init__(self, segments: list[Segment] | None = None, error: str = ""):
        self.segments = segments if segments is not None else [
            Segment(start=0.0, end=3.0, text="이번 주 배포 일정 논의하겠습니다."),
            Segment(start=3.5, end=7.0, text="배포는 금요일로 하고 릴리스 노트는 제가 목요일까지 쓰겠습니다."),
        ]
        self.error = error
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path, language: str = "ko", progress: ProgressCb | None = None) -> Transcript:
        self.calls.append(Path(audio_path))
        if self.error:
            raise STTError(self.error)
        if progress:
            progress(0.5)
            progress(1.0)
        return Transcript(engine="fake", language=language, segments=[s.model_copy() for s in self.segments])
