"""네이버 CLOVA Speech — 장문 인식 REST API (비동기 요청 + 결과 조회, 화자 분리).

POST {invoke_url}/recognizer/upload
  headers: X-CLOVASPEECH-API-KEY: <secret>
  form:    media=<파일>, params={"language": "ko-KR", "completion": "async", "diarization": {...}, ...}
  → {"token": "..."}
GET  {invoke_url}/recognizer/{token}
  → {"result": "COMPLETED", "segments": [{"start": ms, "end": ms, "text": "...", "speaker": {"label": "1"}}]}

※ NAVER Cloud 공개 예제 형식을 따라 구현했다. 실제 키로 한 번 점검할 것(README 참고).
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import requests

from ...models import Segment, Transcript
from .base import ProgressCb, STTError
from .cloud import locale_for, prepare_upload, renumber_speakers

UPLOAD_EXTS = {".mp3", ".aac", ".ac3", ".ogg", ".flac", ".wav", ".m4a"}
CHUNK_SEC = 3 * 3600
POLL_SEC = 5.0
TIMEOUT_SEC = 3 * 3600
DONE = "COMPLETED"
FAILED = {"FAILED", "ERROR"}


class ClovaTranscriber:
    name = "clova"

    def __init__(self, invoke_url: str, secret: str, max_speakers: int = 0, session=None, poll_sec: float = POLL_SEC, sleep=time.sleep):
        self.invoke_url = invoke_url.rstrip("/")
        self.secret = secret
        self.max_speakers = max_speakers
        self.session = session or requests.Session()
        self.poll_sec = poll_sec
        self._sleep = sleep

    def _headers(self) -> dict:
        return {"Accept": "application/json;UTF-8", "X-CLOVASPEECH-API-KEY": self.secret}

    def _params(self, language: str) -> dict:
        return {
            "language": locale_for(language),
            "completion": "async",
            "wordAlignment": False,
            "fullText": True,
            "diarization": {
                "enable": self.max_speakers != 1,
                "speakerCountMin": -1,
                "speakerCountMax": self.max_speakers or -1,
            },
        }

    def _check(self, resp: requests.Response) -> dict:
        if resp.status_code in (401, 403):
            raise STTError("CLOVA Speech Secret Key 또는 Invoke URL 이 올바르지 않습니다.")
        if not resp.ok:
            raise STTError(f"CLOVA Speech 오류({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def _recognize(self, path: Path, language: str, progress: ProgressCb | None) -> dict:
        try:
            with open(path, "rb") as fh:
                resp = self.session.post(
                    f"{self.invoke_url}/recognizer/upload",
                    headers=self._headers(),
                    files={
                        "media": (path.name, fh),
                        "params": (None, json.dumps(self._params(language), ensure_ascii=False).encode("utf-8"), "application/json"),
                    },
                    timeout=600,
                )
            data = self._check(resp)
            token = data.get("token")
            if not token:  # 동기 응답으로 온 경우
                return data
            waited = 0.0
            while waited < TIMEOUT_SEC:
                result = self._check(self.session.get(f"{self.invoke_url}/recognizer/{token}", headers=self._headers(), timeout=60))
                status = str(result.get("result", "")).upper()
                if status == DONE:
                    return result
                if status in FAILED:
                    raise STTError(f"CLOVA Speech 인식 실패: {result.get('message', status)}")
                if progress and isinstance(result.get("progress"), (int, float)):
                    progress(min(0.99, result["progress"] / 100))
                self._sleep(self.poll_sec)
                waited += self.poll_sec
        except requests.RequestException as exc:
            raise STTError(f"CLOVA Speech 에 연결하지 못했습니다: {exc}") from exc
        raise STTError("CLOVA Speech 결과 대기 시간이 초과되었습니다.")

    def transcribe(self, audio_path: Path, language: str = "ko", progress: ProgressCb | None = None) -> Transcript:
        if not self.invoke_url or not self.secret:
            raise STTError("CLOVA Speech Invoke URL 과 Secret Key 를 설정 탭에서 입력하세요.")
        segments: list[Segment] = []
        duration = 0.0
        with tempfile.TemporaryDirectory() as tmp:
            chunks = prepare_upload(Path(audio_path), Path(tmp), UPLOAD_EXTS, CHUNK_SEC)
            for index, (offset, chunk) in enumerate(chunks):
                def chunk_progress(p: float, i: int = index) -> None:
                    if progress:
                        progress((i + p) / len(chunks))

                data = self._recognize(chunk, language, chunk_progress)
                for seg in data.get("segments", []):
                    text = (seg.get("text") or "").strip()
                    if not text:
                        continue
                    label = (seg.get("speaker") or {}).get("label") or (seg.get("diarization") or {}).get("label") or ""
                    key = f"{index}:{label}" if label and len(chunks) > 1 else str(label)
                    start = offset + seg.get("start", 0) / 1000
                    end = offset + seg.get("end", 0) / 1000
                    segments.append(Segment(start=start, end=end, text=text, speaker=key))
                    duration = max(duration, end)
                chunk_progress(1.0)
        return Transcript(engine="clova", language=language, duration_sec=duration, segments=renumber_speakers(segments))
