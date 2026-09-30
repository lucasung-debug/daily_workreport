"""사용자 설정 (config.json). API 키 같은 비밀값은 credentials.py 가 따로 보관한다."""

from __future__ import annotations

import json
import logging
from datetime import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from . import paths

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"


class CategoryRule(BaseModel):
    field: Literal["app", "title"] = "title"
    pattern: str  # 정규식 (대소문자 무시)
    category: str


class Settings(BaseModel):
    # 일반
    user_name: str = ""  # 회의록 액션아이템 담당자 매칭용
    work_start: str = "09:00"
    work_end: str = "18:00"
    draft_minutes_before_end: int = 10  # 퇴근 N분 전에 초안 자동 생성
    auto_draft_enabled: bool = True
    track_outside_work_hours: bool = False
    autostart: bool = False

    # 활동 수집
    poll_interval_sec: float = 2.0
    idle_threshold_sec: int = 300
    excluded_apps: list[str] = Field(default_factory=lambda: ["KeePass.exe", "1Password.exe"])
    excluded_title_keywords: list[str] = Field(
        default_factory=lambda: ["InPrivate", "Incognito", "시크릿", "인터넷뱅킹", "뱅킹"]
    )
    category_rules: list[CategoryRule] = Field(default_factory=list)

    # 스크린샷 (기본 OFF)
    screenshot_enabled: bool = False
    screenshot_interval_min: int = 10
    keep_screenshot_images: bool = False

    # 회의
    recorder_watch_enabled: bool = True
    recorder_dir: str = ""  # 비어 있으면 문서\Sound Recordings 자동 탐지
    recorder_watch_since: float = 0.0  # 이 시각 이후의 녹음만 가져온다(처음 켤 때 자동 설정)
    meeting_detect_enabled: bool = True
    meeting_apps: list[str] = Field(
        default_factory=lambda: ["Teams", "Zoom", "Webex", "Google Meet", "Meet -", "Meet –", "Slack", "Discord"]
    )
    record_mic_device: str = ""  # 비어 있으면 기본 마이크
    record_speaker_device: str = ""  # 비어 있으면 기본 스피커(루프백)
    record_split_channels: bool = True  # 왼쪽=내 마이크, 오른쪽=PC 소리 → '나/상대방' 구분
    audio_retention_days: int = 30

    # STT
    stt_engine: Literal["whisper_local", "azure", "clova"] = "whisper_local"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"
    whisper_model_dir: str = ""  # 사내망에서 다운로드가 막힐 때 수동 지정
    azure_region: str = "koreacentral"
    azure_endpoint: str = ""  # 비어 있으면 https://{region}.api.cognitive.microsoft.com
    clova_invoke_url: str = ""
    max_speakers: int = 0  # 0 = 자동

    # Claude
    claude_model: str = DEFAULT_MODEL
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_fallbacks: bool = True

    def work_start_time(self) -> time:
        return _parse_hhmm(self.work_start, time(9, 0))

    def work_end_time(self) -> time:
        return _parse_hhmm(self.work_end, time(18, 0))

    def recorder_path(self) -> Path:
        return Path(self.recorder_dir) if self.recorder_dir else paths.default_sound_recorder_dir()


def _parse_hhmm(value: str, default: time) -> time:
    try:
        hh, mm = value.strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return default


def load_settings(path: Path | None = None) -> Settings:
    path = path or paths.config_path()
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError, OSError) as exc:
        log.warning("설정 파일을 읽지 못해 기본값을 사용합니다: %s", exc)
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or paths.config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
