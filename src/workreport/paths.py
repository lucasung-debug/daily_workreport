"""데이터·설정·오디오·모델 경로.

- 설정: %APPDATA%\\WorkReport (로밍)
- DB·오디오·모델·로그: %LOCALAPPDATA%\\WorkReport
- 환경 변수 WORKREPORT_HOME 이 있으면 모든 경로를 그 아래로 모은다(테스트·포터블 실행용).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from . import APP_NAME

# Windows Known Folder ID: 문서(Documents). OneDrive 로 리디렉션된 경우에도 실제 경로를 돌려준다.
_FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"


def _home_override() -> Path | None:
    value = os.environ.get("WORKREPORT_HOME")
    return Path(value) if value else None


def app_config_dir() -> Path:
    override = _home_override()
    if override:
        return override
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def app_data_dir() -> Path:
    override = _home_override()
    if override:
        return override
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return _ensure(app_config_dir()) / "config.json"


def db_path() -> Path:
    return _ensure(app_data_dir()) / "workreport.db"


def audio_dir() -> Path:
    return _ensure(app_data_dir() / "audio")


def screenshots_dir() -> Path:
    return _ensure(app_data_dir() / "screenshots")


def models_dir() -> Path:
    return _ensure(app_data_dir() / "models")


def log_dir() -> Path:
    return _ensure(app_data_dir() / "logs")


def _known_folder(folder_id: str) -> Path | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    u = uuid.UUID(folder_id)
    guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    out = ctypes.c_wchar_p()
    try:
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        if hr != 0 or not out.value:
            return None
        return Path(out.value)
    except OSError:
        return None
    finally:
        if out:
            ctypes.windll.ole32.CoTaskMemFree(out)


def documents_dir() -> Path:
    return _known_folder(_FOLDERID_DOCUMENTS) or Path.home() / "Documents"


def sound_recorder_dir_candidates() -> list[Path]:
    """Windows 11 녹음기 앱의 기본 저장 폴더 후보 (문서\\Sound Recordings)."""
    docs = documents_dir()
    return [docs / "Sound Recordings", docs / "녹음", docs / "Sound recordings"]


def default_sound_recorder_dir() -> Path:
    for candidate in sound_recorder_dir_candidates():
        if candidate.is_dir():
            return candidate
    return sound_recorder_dir_candidates()[0]
