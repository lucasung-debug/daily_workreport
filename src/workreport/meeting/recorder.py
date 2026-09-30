"""앱 내장 회의 녹음: 마이크 + PC 소리(WASAPI 루프백).

- 두 입력을 각각의 스레드에서 16kHz 모노로 받아(soundcard 가 OS 리샘플링 사용) FLAC 으로 저장한다.
- record_split_channels=True 이면 2채널 파일(왼쪽=내 마이크, 오른쪽=PC 소리, 파일명 *_dual.flac)로 저장해
  STT 단계에서 '나' / '상대방' 을 구분한다. False 이면 두 소리를 섞은 모노 파일로 저장한다.
- 실시간 인식은 하지 않는다. 녹음이 끝나면 파일을 파이프라인에 넘긴다.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .. import paths
from ..config import Settings
from .audio import DUAL_SUFFIX, TARGET_RATE

log = logging.getLogger(__name__)

BLOCK = TARGET_RATE // 10  # 0.1초
MAX_LAG_SEC = 3.0


def _com_init() -> None:
    """soundcard(WASAPI)를 쓰는 스레드마다 COM 을 초기화한다."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED


class SoundcardBackend:
    """soundcard 라이브러리 어댑터 (Windows 전용)."""

    def _sc(self):
        _com_init()
        import soundcard

        return soundcard

    def microphone(self, name: str = ""):
        sc = self._sc()
        return sc.get_microphone(name) if name else sc.default_microphone()

    def loopback(self, speaker_name: str = ""):
        sc = self._sc()
        speaker = sc.get_speaker(speaker_name) if speaker_name else sc.default_speaker()
        return sc.get_microphone(str(speaker.name), include_loopback=True)

    def list_devices(self) -> tuple[list[str], list[str]]:
        sc = self._sc()
        return [m.name for m in sc.all_microphones()], [s.name for s in sc.all_speakers()]


class _Capture(threading.Thread):
    def __init__(self, source_factory: Callable[[], object], name: str, stop: threading.Event):
        super().__init__(name=name, daemon=True)
        self._factory = source_factory
        self._stop_event = stop
        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self.available = 0
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            _com_init()
            source = self._factory()
            with source.recorder(samplerate=TARGET_RATE, channels=1, blocksize=BLOCK) as rec:
                while not self._stop_event.is_set():
                    data = np.asarray(rec.record(numframes=BLOCK), dtype=np.float32).reshape(-1)
                    with self._lock:
                        self._blocks.append(data)
                        self.available += data.size
        except Exception as exc:  # 장치 분리 등
            log.exception("녹음 입력 오류(%s)", self.name)
            self.error = exc

    def take(self, n: int) -> np.ndarray:
        with self._lock:
            buf = np.concatenate(self._blocks) if self._blocks else np.zeros(0, np.float32)
            out, rest = buf[:n], buf[n:]
            self._blocks = [rest] if rest.size else []
            self.available = rest.size
        if out.size < n:
            out = np.concatenate([out, np.zeros(n - out.size, np.float32)])
        return out


class MeetingRecorder:
    def __init__(self, settings: Callable[[], Settings], backend=None, clock: Callable[[], float] = time.time):
        self._settings = settings
        self.backend = backend or SoundcardBackend()
        self._clock = clock
        self._stop = threading.Event()
        self._captures: list[_Capture] = []
        self._writer: threading.Thread | None = None
        self.path: Path | None = None
        self.started_at: float | None = None
        self.title_hint = ""
        self.error: str = ""
        self.on_error: Callable[[str], None] | None = None

    @property
    def is_recording(self) -> bool:
        return self._writer is not None and self._writer.is_alive()

    def elapsed(self) -> float:
        return self._clock() - self.started_at if self.started_at and self.is_recording else 0.0

    def start(self, title_hint: str = "") -> Path:
        if self.is_recording:
            raise RuntimeError("이미 녹음 중입니다.")
        settings = self._settings()
        split = settings.record_split_channels
        self.started_at = self._clock()
        self.title_hint = title_hint
        self.error = ""
        stamp = datetime.fromtimestamp(self.started_at)
        folder = paths.audio_dir() / stamp.strftime("%Y-%m")
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"meeting_{stamp:%Y%m%d_%H%M%S}{DUAL_SUFFIX if split else ''}.flac"

        self._stop.clear()
        mic_name, speaker_name = settings.record_mic_device, settings.record_speaker_device
        self._captures = [
            _Capture(lambda: self.backend.microphone(mic_name), "rec-mic", self._stop),
            _Capture(lambda: self.backend.loopback(speaker_name), "rec-loopback", self._stop),
        ]
        for cap in self._captures:
            cap.start()
        self._writer = threading.Thread(target=self._write_loop, args=(self.path, split), name="rec-writer", daemon=True)
        self._writer.start()
        log.info("회의 녹음 시작: %s", self.path)
        return self.path

    def stop(self) -> tuple[Path, float, float]:
        if not self._writer:
            raise RuntimeError("녹음 중이 아닙니다.")
        self._stop.set()
        for cap in self._captures:
            cap.join(timeout=5)
        self._writer.join(timeout=10)
        self._writer = None
        ended = self._clock()
        log.info("회의 녹음 종료: %s", self.path)
        return self.path, self.started_at, ended

    def _write_loop(self, path: Path, split: bool) -> None:
        mic, loop = self._captures
        channels = 2 if split else 1
        max_lag = int(MAX_LAG_SEC * TARGET_RATE)
        try:
            with sf.SoundFile(str(path), "w", samplerate=TARGET_RATE, channels=channels, subtype="PCM_16", format="FLAC") as out:
                while True:
                    stopping = self._stop.is_set() or all(c.error or not c.is_alive() for c in self._captures)
                    if not stopping:
                        time.sleep(0.2)
                    a, b = mic.available, loop.available
                    live = [c for c in (mic, loop) if c.error is None]
                    if not live:
                        break
                    # 한쪽 입력이 멈췄거나 크게 뒤처지면 무음으로 채운다
                    if stopping or mic.error or loop.error or abs(a - b) > max_lag:
                        n = max(a, b) if stopping else max(a, b) - TARGET_RATE
                    else:
                        n = min(a, b)
                    if n > 0:
                        left, right = mic.take(n), loop.take(n)
                        frame = np.stack([left, right], axis=1) if split else np.clip(left + right, -1.0, 1.0)
                        out.write(frame)
                    if stopping:
                        break
            errors = [str(c.error) for c in self._captures if c.error]
            if errors:
                self.error = "녹음 장치 오류: " + "; ".join(errors)
                if self.on_error:
                    self.on_error(self.error)
        except Exception as exc:
            log.exception("녹음 파일 저장 실패")
            self.error = f"녹음 파일 저장 실패: {exc}"
            if self.on_error:
                self.on_error(self.error)
