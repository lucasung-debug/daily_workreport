"""오디오 파일 유틸리티: 길이 확인, 16kHz 모노 FLAC 변환, 긴 파일 분할, 2채널(나/상대방) 분리.

- WAV·FLAC·OGG·MP3 는 soundfile(libsndfile) 로 읽는다.
- 녹음기 앱 기본 형식인 m4a(AAC)·wma 등은 PyAV 로 디코딩한다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

TARGET_RATE = 16000
DUAL_SUFFIX = "_dual"  # 앱 내장 녹음의 2채널 파일: 왼쪽=내 마이크, 오른쪽=PC 소리
CHANNEL_LABELS = ("나", "상대방")
SILENCE_RMS = 1e-3


def is_dual_channel_recording(path: Path) -> bool:
    return Path(path).stem.endswith(DUAL_SUFFIX)


def _soundfile_readable(path: Path) -> bool:
    try:
        sf.info(str(path))
        return True
    except Exception:
        return False


def probe_duration(path: Path) -> float:
    path = Path(path)
    try:
        return float(sf.info(str(path)).duration)
    except Exception:
        pass
    try:
        import av

        with av.open(str(path)) as container:
            if container.duration:
                return container.duration / 1_000_000
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except Exception as exc:
        log.warning("오디오 길이를 알 수 없습니다(%s): %s", path.name, exc)
    return 0.0


def iter_mono_16k(path: Path, block_sec: float = 30.0) -> Iterator[np.ndarray]:
    """어떤 형식이든 16kHz 모노 float32 블록으로 디코딩한다."""
    path = Path(path)
    if _soundfile_readable(path) and sf.info(str(path)).samplerate == TARGET_RATE:
        for block in sf.blocks(str(path), blocksize=int(TARGET_RATE * block_sec), dtype="float32", always_2d=True):
            yield block.mean(axis=1)
        return
    import av  # 샘플레이트 변환과 m4a 디코딩

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=TARGET_RATE)
        pending: list[np.ndarray] = []
        size = 0
        limit = int(TARGET_RATE * block_sec)

        def drain(frames):
            nonlocal size
            for out in frames:
                arr = out.to_ndarray().reshape(-1).astype(np.float32)
                pending.append(arr)
                size += arr.size

        for frame in container.decode(stream):
            drain(resampler.resample(frame))
            if size >= limit:
                yield np.concatenate(pending)
                pending.clear()
                size = 0
        drain(resampler.resample(None))
        if pending:
            yield np.concatenate(pending)


def convert_to_flac_16k(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(str(dst), "w", samplerate=TARGET_RATE, channels=1, subtype="PCM_16", format="FLAC") as out:
        for block in iter_mono_16k(src):
            out.write(block)
    return dst


def chunk_audio(src: Path, max_sec: float, out_dir: Path) -> list[tuple[float, Path]]:
    """max_sec 보다 긴 파일을 16kHz 모노 FLAC 조각으로 나눈다. [(시작 오프셋 초, 경로)]"""
    if probe_duration(src) <= max_sec:
        return [(0.0, Path(src))]
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[float, Path]] = []
    limit = int(max_sec * TARGET_RATE)
    writer: sf.SoundFile | None = None
    written = 0
    offset = 0.0
    try:
        for block in iter_mono_16k(src, block_sec=10):
            pos = 0
            while pos < block.size:
                if writer is None or written >= limit:
                    if writer is not None:
                        writer.close()
                        offset += written / TARGET_RATE
                    path = out_dir / f"chunk_{len(chunks):03d}.flac"
                    writer = sf.SoundFile(str(path), "w", samplerate=TARGET_RATE, channels=1, subtype="PCM_16", format="FLAC")
                    chunks.append((offset, path))
                    written = 0
                take = min(block.size - pos, limit - written)
                writer.write(block[pos : pos + take])
                written += take
                pos += take
    finally:
        if writer is not None:
            writer.close()
    return chunks


def split_channels(src: Path, out_dir: Path) -> list[tuple[str, Path, float]]:
    """2채널 녹음을 채널별 모노 FLAC 으로 나눈다. [(라벨, 경로, RMS)]"""
    out_dir.mkdir(parents=True, exist_ok=True)
    info = sf.info(str(src))
    outputs = []
    writers = []
    sums = [0.0] * info.channels
    count = 0
    try:
        for ch in range(info.channels):
            label = CHANNEL_LABELS[ch] if ch < len(CHANNEL_LABELS) else f"채널 {ch + 1}"
            path = out_dir / f"channel_{ch}.flac"
            writers.append(sf.SoundFile(str(path), "w", samplerate=info.samplerate, channels=1, subtype="PCM_16", format="FLAC"))
            outputs.append((label, path))
        for block in sf.blocks(str(src), blocksize=info.samplerate * 30, dtype="float32", always_2d=True):
            count += block.shape[0]
            for ch, writer in enumerate(writers):
                writer.write(block[:, ch])
                sums[ch] += float(np.square(block[:, ch], dtype=np.float64).sum())
    finally:
        for writer in writers:
            writer.close()
    return [(label, path, (sums[i] / count) ** 0.5 if count else 0.0) for i, (label, path) in enumerate(outputs)]
