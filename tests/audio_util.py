"""테스트용 오디오 파일 생성."""

from pathlib import Path

import numpy as np
import soundfile as sf


def tone(seconds: float, rate: int = 16000, freq: float = 440.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def write_wav(path: Path, data: np.ndarray, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, rate)
    return path


def write_m4a(path: Path, seconds: float = 2.0, rate: int = 48000) -> Path:
    """Windows 11 녹음기 앱과 같은 AAC/m4a 파일을 만든다."""
    import av

    samples = (tone(seconds, rate) * 32767).astype(np.int16)
    with av.open(str(path), "w", format="mp4") as container:
        stream = container.add_stream("aac", rate=rate)
        stream.layout = "mono"
        frame_size = 1024
        for i in range(0, samples.size, frame_size):
            chunk = samples[i : i + frame_size].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="s16", layout="mono")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path
