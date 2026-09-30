"""STT 엔진 공통 인터페이스.

실시간 인식은 하지 않는다. 녹음이 끝난 파일을 통째로 변환한다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Protocol

from ...config import Settings
from ...models import Segment, Transcript
from .. import audio

ProgressCb = Callable[[float], None]


class STTError(Exception):
    """사용자에게 보여줄 수 있는 STT 오류."""


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio_path: Path, language: str = "ko", progress: ProgressCb | None = None) -> Transcript:
        ...


def create_transcriber(settings: Settings) -> Transcriber:
    from ... import credentials

    if settings.stt_engine == "azure":
        from .azure import AzureTranscriber

        return AzureTranscriber(
            key=credentials.get_secret(credentials.AZURE_SPEECH_KEY),
            region=settings.azure_region,
            endpoint=settings.azure_endpoint,
            max_speakers=settings.max_speakers,
        )
    if settings.stt_engine == "clova":
        from .clova import ClovaTranscriber

        return ClovaTranscriber(
            invoke_url=settings.clova_invoke_url,
            secret=credentials.get_secret(credentials.CLOVA_SECRET),
            max_speakers=settings.max_speakers,
        )
    from .whisper_local import WhisperLocalTranscriber

    return WhisperLocalTranscriber(
        model_size=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        model_dir=settings.whisper_model_dir,
    )


def _merge_label(channel: str, speaker: str, distinct: int) -> str:
    if not channel:
        return speaker
    if speaker and distinct > 1:
        return f"{channel}·{speaker}"
    return channel


def transcribe_recording(
    transcriber: Transcriber,
    audio_path: Path,
    language: str = "ko",
    progress: ProgressCb | None = None,
    work_dir: Path | None = None,
) -> Transcript:
    """녹음 파일 하나를 전사한다. 2채널(나/상대방) 녹음이면 채널별로 변환해 화자를 붙인다."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise STTError(f"오디오 파일을 찾을 수 없습니다: {audio_path}")
    duration = audio.probe_duration(audio_path)

    if not audio.is_dual_channel_recording(audio_path):
        transcript = transcriber.transcribe(audio_path, language, progress)
        transcript.duration_sec = transcript.duration_sec or duration
        return transcript

    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        parts = [(label, path) for label, path, rms in audio.split_channels(audio_path, Path(tmp)) if rms >= audio.SILENCE_RMS]
        segments: list[Segment] = []
        engine = transcriber.name
        for index, (label, path) in enumerate(parts):
            def part_progress(p: float, i: int = index) -> None:
                if progress:
                    progress((i + p) / len(parts))

            part = transcriber.transcribe(path, language, part_progress)
            engine = part.engine or engine
            distinct = len({s.speaker for s in part.segments if s.speaker})
            for seg in part.segments:
                seg.speaker = _merge_label(label, seg.speaker, distinct)
                segments.append(seg)
    segments.sort(key=lambda s: (s.start, s.end))
    if progress:
        progress(1.0)
    return Transcript(engine=engine, language=language, duration_sec=duration, segments=segments)
