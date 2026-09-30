"""Azure AI Speech — fast transcription REST API (동기, 화자 분리).

POST {endpoint}/speechtotext/transcriptions:transcribe?api-version=2025-10-15
  headers: Ocp-Apim-Subscription-Key
  form:    audio=<파일>, definition={"locales": ["ko-KR"], "diarization": {"enabled": true, "maxSpeakers": N}}
응답: phrases[].offsetMilliseconds / durationMilliseconds / text / speaker
제한: 5시간·500MB 미만 (여기서는 2시간 단위로 나눠 보낸다)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import requests

from ...models import Segment, Transcript
from .base import ProgressCb, STTError
from .cloud import locale_for, prepare_upload, renumber_speakers

API_VERSION = "2025-10-15"
UPLOAD_EXTS = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".wma", ".aac", ".amr", ".webm", ".spx"}
CHUNK_SEC = 2 * 3600
DEFAULT_MAX_SPEAKERS = 6


class AzureTranscriber:
    name = "azure"

    def __init__(self, key: str, region: str = "koreacentral", endpoint: str = "", max_speakers: int = 0, session=None, timeout: float = 3600):
        self.key = key
        self.region = region
        self.endpoint = endpoint
        self.max_speakers = max_speakers
        self.session = session or requests.Session()
        self.timeout = timeout

    @property
    def url(self) -> str:
        base = (self.endpoint or f"https://{self.region}.api.cognitive.microsoft.com").rstrip("/")
        return f"{base}/speechtotext/transcriptions:transcribe?api-version={API_VERSION}"

    def _definition(self, language: str) -> dict:
        definition: dict = {"locales": [locale_for(language)]}
        if self.max_speakers != 1:
            definition["diarization"] = {"enabled": True, "maxSpeakers": self.max_speakers or DEFAULT_MAX_SPEAKERS}
        return definition

    def _request(self, path: Path, language: str) -> dict:
        try:
            with open(path, "rb") as fh:
                resp = self.session.post(
                    self.url,
                    headers={"Ocp-Apim-Subscription-Key": self.key},
                    files={
                        "audio": (path.name, fh),
                        "definition": (None, json.dumps(self._definition(language))),
                    },
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise STTError(f"Azure Speech 에 연결하지 못했습니다: {exc}") from exc
        if resp.status_code in (401, 403):
            raise STTError("Azure Speech 키 또는 지역(엔드포인트)이 올바르지 않습니다.")
        if resp.status_code == 429:
            raise STTError("Azure Speech 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
        if not resp.ok:
            raise STTError(f"Azure Speech 오류({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def transcribe(self, audio_path: Path, language: str = "ko", progress: ProgressCb | None = None) -> Transcript:
        if not self.key:
            raise STTError("Azure Speech 키가 설정되지 않았습니다. 설정 탭에서 입력하세요.")
        segments: list[Segment] = []
        duration = 0.0
        with tempfile.TemporaryDirectory() as tmp:
            chunks = prepare_upload(Path(audio_path), Path(tmp), UPLOAD_EXTS, CHUNK_SEC)
            for index, (offset, chunk) in enumerate(chunks):
                data = self._request(chunk, language)
                for phrase in data.get("phrases", []):
                    start = offset + phrase.get("offsetMilliseconds", 0) / 1000
                    end = start + phrase.get("durationMilliseconds", 0) / 1000
                    text = (phrase.get("text") or "").strip()
                    speaker = phrase.get("speaker")
                    if text:
                        # 조각마다 화자 번호가 따로 매겨지므로 조각 번호를 붙여 구분한다
                        key = "" if speaker is None else (f"{index}:{speaker}" if len(chunks) > 1 else str(speaker))
                        segments.append(Segment(start=start, end=end, text=text, speaker=key))
                duration = offset + data.get("durationMilliseconds", 0) / 1000
                if progress:
                    progress((index + 1) / len(chunks))
        return Transcript(engine="azure", language=language, duration_sec=duration, segments=renumber_speakers(segments))
