"""클라우드 STT 공통: 업로드용 파일 준비(형식 변환·분할), 화자 번호 정리."""

from __future__ import annotations

from pathlib import Path

from ...models import Segment
from .. import audio

LOCALES = {"ko": "ko-KR", "en": "en-US", "ja": "ja-JP", "zh": "zh-CN"}


def locale_for(language: str) -> str:
    return LOCALES.get(language, language)


def prepare_upload(src: Path, work_dir: Path, allowed_exts: set[str], max_sec: float) -> list[tuple[float, Path]]:
    """서비스가 받는 형식이 아니면 16kHz 모노 FLAC 으로 바꾸고, 너무 길면 나눈다."""
    src = Path(src)
    if src.suffix.lower() not in allowed_exts:
        src = audio.convert_to_flac_16k(src, work_dir / "upload.flac")
    return audio.chunk_audio(src, max_sec, work_dir / "chunks")


def renumber_speakers(segments: list[Segment]) -> list[Segment]:
    """엔진별 화자 ID(0/1/'A' 등)를 등장 순서대로 '화자 1', '화자 2'… 로 바꾼다."""
    mapping: dict[str, str] = {}
    for seg in segments:
        if seg.speaker:
            seg.speaker = mapping.setdefault(seg.speaker, f"화자 {len(mapping) + 1}")
    return segments
