import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from audio_util import tone, write_m4a, write_wav
from workreport.meeting import audio
from workreport.meeting.stt.azure import API_VERSION, AzureTranscriber
from workreport.meeting.stt.base import STTError, _merge_label, transcribe_recording
from workreport.meeting.stt.clova import ClovaTranscriber
from workreport.meeting.stt.cloud import renumber_speakers
from workreport.meeting.stt.fake import FakeTranscriber
from workreport.models import Segment


def dual_file(tmp_path, right_silent=True):
    left = tone(3)
    right = np.zeros_like(left) if right_silent else tone(3, freq=880)
    path = tmp_path / "meeting_20260930_140000_dual.flac"
    sf.write(str(path), np.stack([left, right], axis=1), 16000, format="FLAC", subtype="PCM_16")
    return path


def test_probe_and_dual_detection(tmp_path):
    path = dual_file(tmp_path)
    assert audio.is_dual_channel_recording(path)
    assert not audio.is_dual_channel_recording(tmp_path / "녹음.m4a")
    assert audio.probe_duration(path) == pytest.approx(3.0, abs=0.01)


def test_m4a_decode_and_convert(tmp_path):
    m4a = write_m4a(tmp_path / "녹음.m4a", seconds=2.0)
    assert audio.probe_duration(m4a) == pytest.approx(2.0, abs=0.1)
    out = audio.convert_to_flac_16k(m4a, tmp_path / "out.flac")
    info = sf.info(str(out))
    assert info.samplerate == 16000 and info.channels == 1
    assert info.duration == pytest.approx(2.0, abs=0.1)


def test_chunk_audio(tmp_path):
    src = write_wav(tmp_path / "long.wav", tone(5))
    assert audio.chunk_audio(src, 10, tmp_path / "c") == [(0.0, src)]
    chunks = audio.chunk_audio(src, 2, tmp_path / "c")
    assert [round(o) for o, _ in chunks] == [0, 2, 4]
    assert sum(sf.info(str(p)).duration for _, p in chunks) == pytest.approx(5.0, abs=0.01)


def test_split_channels_rms(tmp_path):
    parts = audio.split_channels(dual_file(tmp_path), tmp_path / "split")
    assert [p[0] for p in parts] == ["나", "상대방"]
    assert parts[0][2] > 0.1 and parts[1][2] < audio.SILENCE_RMS


def test_transcribe_single_file(tmp_path):
    fake = FakeTranscriber()
    t = transcribe_recording(fake, write_wav(tmp_path / "a.wav", tone(1)))
    assert len(t.segments) == 2 and t.duration_sec == pytest.approx(1.0)


def test_transcribe_dual_skips_silent_channel(tmp_path):
    fake = FakeTranscriber()
    progress = []
    t = transcribe_recording(fake, dual_file(tmp_path), progress=progress.append, work_dir=tmp_path)
    assert len(fake.calls) == 1
    assert {s.speaker for s in t.segments} == {"나"}
    assert progress[-1] == 1.0


def test_transcribe_dual_merges_both_channels(tmp_path):
    fake = FakeTranscriber([Segment(start=1, end=2, text="a"), Segment(start=5, end=6, text="b")])
    t = transcribe_recording(fake, dual_file(tmp_path, right_silent=False), work_dir=tmp_path)
    assert [(s.start, s.speaker) for s in t.segments] == [(1, "나"), (1, "상대방"), (5, "나"), (5, "상대방")]


def test_missing_file(tmp_path):
    with pytest.raises(STTError):
        transcribe_recording(FakeTranscriber(), tmp_path / "none.m4a")


def test_label_merge_and_renumber():
    assert _merge_label("상대방", "화자 1", 2) == "상대방·화자 1"
    assert _merge_label("상대방", "화자 1", 1) == "상대방"
    assert _merge_label("", "화자 2", 2) == "화자 2"
    segs = renumber_speakers([Segment(start=0, end=1, text="x", speaker="0"), Segment(start=1, end=2, text="y", speaker="3"), Segment(start=2, end=3, text="z", speaker="0")])
    assert [s.speaker for s in segs] == ["화자 1", "화자 2", "화자 1"]


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.ok = status < 400
        self._data = data or {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, posts=(), gets=()):
        self.posts, self.gets = list(posts), list(gets)
        self.post_calls, self.get_calls = [], []

    def post(self, url, headers=None, files=None, timeout=None):
        self.post_calls.append(SimpleNamespace(url=url, headers=headers, files={k: (v[0], v[1] if not hasattr(v[1], "read") else "<file>") for k, v in files.items()}))
        return self.posts.pop(0)

    def get(self, url, headers=None, timeout=None):
        self.get_calls.append(url)
        return self.gets.pop(0)


def test_azure_request_and_parse(tmp_path):
    body = {
        "durationMilliseconds": 5000,
        "phrases": [
            {"offsetMilliseconds": 0, "durationMilliseconds": 1500, "text": "안녕하세요", "speaker": 1},
            {"offsetMilliseconds": 2000, "durationMilliseconds": 1000, "text": "네 반갑습니다", "speaker": 2},
            {"offsetMilliseconds": 3500, "durationMilliseconds": 500, "text": " ", "speaker": 2},
        ],
    }
    session = FakeSession(posts=[FakeResponse(200, body)])
    m4a = write_m4a(tmp_path / "녹음.m4a")
    t = AzureTranscriber(key="k", region="koreacentral", session=session).transcribe(m4a)
    call = session.post_calls[0]
    assert call.url == f"https://koreacentral.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe?api-version={API_VERSION}"
    assert call.headers == {"Ocp-Apim-Subscription-Key": "k"}
    assert call.files["audio"][0] == "upload.flac"  # m4a 는 FLAC 으로 변환해 올린다
    assert json.loads(call.files["definition"][1]) == {"locales": ["ko-KR"], "diarization": {"enabled": True, "maxSpeakers": 6}}
    assert [(s.start, s.end, s.speaker) for s in t.segments] == [(0, 1.5, "화자 1"), (2, 3, "화자 2")]
    assert t.duration_sec == 5


def test_azure_errors(tmp_path):
    wav = write_wav(tmp_path / "a.wav", tone(1))
    with pytest.raises(STTError, match="키가 설정"):
        AzureTranscriber(key="").transcribe(wav)
    with pytest.raises(STTError, match="올바르지"):
        AzureTranscriber(key="k", session=FakeSession(posts=[FakeResponse(401)])).transcribe(wav)
    custom = AzureTranscriber(key="k", endpoint="https://my.cognitiveservices.azure.com/", session=FakeSession(posts=[FakeResponse(200, {})]))
    custom.transcribe(wav)
    assert custom.session.post_calls[0].url.startswith("https://my.cognitiveservices.azure.com/speechtotext/")
    assert custom.session.post_calls[0].files["audio"][0] == "a.wav"  # wav 는 그대로 업로드


def test_clova_async_flow(tmp_path):
    wav = write_wav(tmp_path / "a.wav", tone(1))
    done = {
        "result": "COMPLETED",
        "segments": [
            {"start": 0, "end": 1200, "text": "시작하겠습니다", "speaker": {"label": "2", "name": "B"}},
            {"start": 1500, "end": 2500, "text": "네", "speaker": {"label": "1", "name": "A"}},
        ],
    }
    session = FakeSession(posts=[FakeResponse(200, {"token": "tok"})], gets=[FakeResponse(200, {"result": "PROCESSING", "progress": 40}), FakeResponse(200, done)])
    progress = []
    t = ClovaTranscriber("https://clovaspeech-gw.ncloud.com/external/v1/1234/abcd/", "sec", session=session, sleep=lambda s: None).transcribe(wav, progress=progress.append)
    call = session.post_calls[0]
    assert call.url == "https://clovaspeech-gw.ncloud.com/external/v1/1234/abcd/recognizer/upload"
    assert call.headers["X-CLOVASPEECH-API-KEY"] == "sec"
    params = json.loads(call.files["params"][1])
    assert params["language"] == "ko-KR" and params["completion"] == "async" and params["diarization"]["enable"] is True
    assert session.get_calls == ["https://clovaspeech-gw.ncloud.com/external/v1/1234/abcd/recognizer/tok"] * 2
    assert [(s.start, s.speaker, s.text) for s in t.segments] == [(0, "화자 1", "시작하겠습니다"), (1.5, "화자 2", "네")]
    assert 0.4 in progress and progress[-1] == 1.0


def test_clova_failure(tmp_path):
    wav = write_wav(tmp_path / "a.wav", tone(1))
    session = FakeSession(posts=[FakeResponse(200, {"token": "t"})], gets=[FakeResponse(200, {"result": "FAILED", "message": "bad audio"})])
    with pytest.raises(STTError, match="bad audio"):
        ClovaTranscriber("https://x", "s", session=session, sleep=lambda s: None).transcribe(wav)
    with pytest.raises(STTError, match="Invoke URL"):
        ClovaTranscriber("", "").transcribe(wav)


def test_whisper_wrapper_uses_faster_whisper_api(tmp_path, monkeypatch):
    import faster_whisper

    from workreport.meeting.stt import whisper_local

    created = {}

    class FakeModel:
        def __init__(self, source, **kwargs):
            created["source"], created["kwargs"] = source, kwargs

        def transcribe(self, path, **kwargs):
            created["transcribe"] = kwargs
            segs = [SimpleNamespace(start=0.0, end=2.0, text=" 안녕하세요 "), SimpleNamespace(start=2.0, end=4.0, text="  ")]
            return iter(segs), SimpleNamespace(duration=4.0)

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(whisper_local, "_MODELS", {})
    progress = []
    t = whisper_local.WhisperLocalTranscriber("small").transcribe(write_wav(tmp_path / "a.wav", tone(1)), progress=progress.append)
    assert created["source"] == "small"
    assert created["kwargs"]["device"] == "cpu" and created["kwargs"]["compute_type"] == "int8"
    assert created["kwargs"]["download_root"].endswith("models")
    assert created["transcribe"]["language"] == "ko" and created["transcribe"]["vad_filter"] is True
    assert [s.text for s in t.segments] == ["안녕하세요"]
    assert t.engine == "whisper:small" and t.duration_sec == 4.0
    assert progress[0] == 0.5 and progress[-1] == 1.0


def test_whisper_missing_model_dir(tmp_path, monkeypatch):
    from workreport.meeting.stt import whisper_local

    monkeypatch.setattr(whisper_local, "_MODELS", {})
    with pytest.raises(STTError, match="모델 폴더"):
        whisper_local.WhisperLocalTranscriber(model_dir=str(tmp_path / "none")).transcribe(write_wav(tmp_path / "a.wav", tone(1)))
