"""로컬 faster-whisper (오프라인, 기본 엔진).

- 모델은 첫 사용 시 %LOCALAPPDATA%\\WorkReport\\models 로 내려받는다.
- 사내망에서 Hugging Face 가 막혀 있으면 설정의 '모델 폴더'에 CTranslate2 모델 폴더를 직접 지정한다.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from ... import paths
from ...models import Segment, Transcript
from .base import ProgressCb, STTError

log = logging.getLogger(__name__)

_MODELS: dict[tuple, object] = {}
_LOCK = threading.Lock()


class WhisperLocalTranscriber:
    name = "whisper_local"

    def __init__(self, model_size: str = "small", device: str = "cpu", compute_type: str = "int8", model_dir: str = ""):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model_dir = model_dir

    def _model(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTError("로컬 Whisper 엔진이 설치되어 있지 않습니다. `pip install faster-whisper` 후 다시 시도하세요.") from exc
        source = self.model_dir or self.model_size
        key = (source, self.device, self.compute_type)
        with _LOCK:
            if key not in _MODELS:
                if self.model_dir and not Path(self.model_dir).is_dir():
                    raise STTError(f"Whisper 모델 폴더가 없습니다: {self.model_dir}")
                log.info("Whisper 모델 로드: %s (%s, %s)", source, self.device, self.compute_type)
                try:
                    _MODELS[key] = WhisperModel(
                        source,
                        device=self.device,
                        compute_type=self.compute_type,
                        download_root=str(paths.models_dir()),
                        cpu_threads=max(1, (os.cpu_count() or 2) - 1),
                    )
                except Exception as exc:
                    raise STTError(
                        f"Whisper 모델({source})을 불러오지 못했습니다: {exc}\n"
                        "인터넷(Hugging Face) 접속이 막혀 있다면 설정에서 모델 폴더를 직접 지정하세요."
                    ) from exc
            return _MODELS[key]

    def transcribe(self, audio_path: Path, language: str = "ko", progress: ProgressCb | None = None) -> Transcript:
        model = self._model()
        try:
            segments, info = model.transcribe(str(audio_path), language=language, vad_filter=True, beam_size=5)
            out: list[Segment] = []
            for seg in segments:  # 제너레이터: 순회하면서 실제 변환이 진행된다
                text = seg.text.strip()
                if text:
                    out.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
                if progress and info.duration:
                    progress(min(1.0, seg.end / info.duration))
        except STTError:
            raise
        except Exception as exc:
            raise STTError(f"음성 변환 실패: {exc}") from exc
        if progress:
            progress(1.0)
        return Transcript(engine=f"whisper:{Path(self.model_dir).name if self.model_dir else self.model_size}", language=language, duration_sec=float(info.duration or 0), segments=out)
