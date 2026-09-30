"""API 키 보관. Windows 에서는 keyring(자격 증명 관리자)을 사용한다.

keyring 백엔드가 없는 환경(일부 Linux 개발 환경)에서는 설정 폴더의 credentials.json(권한 600)으로 대체한다.
"""

from __future__ import annotations

import json
import logging
import os

from . import APP_NAME, paths

log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = "anthropic_api_key"
AZURE_SPEECH_KEY = "azure_speech_key"
CLOVA_SECRET = "clova_secret"


def _fallback_file():
    return paths.app_config_dir() / "credentials.json"


def _read_fallback() -> dict[str, str]:
    path = _fallback_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_fallback(data: dict[str, str]) -> None:
    path = _fallback_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _keyring():
    """사용 가능한 keyring 모듈. 백엔드가 없으면 None."""
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception as exc:
        log.debug("keyring 사용 불가: %s", exc)
        return None


def get_secret(name: str) -> str:
    kr = _keyring()
    if kr is not None:
        try:
            return kr.get_password(APP_NAME, name) or ""
        except Exception as exc:
            log.warning("keyring 읽기 실패: %s", exc)
    return _read_fallback().get(name, "")


def set_secret(name: str, value: str) -> None:
    kr = _keyring()
    if kr is not None:
        if value:
            kr.set_password(APP_NAME, name, value)
        else:
            try:
                kr.delete_password(APP_NAME, name)
            except kr.errors.PasswordDeleteError:
                pass
        return
    log.warning("keyring 을 사용할 수 없어 설정 폴더의 credentials.json 에 저장합니다.")
    data = _read_fallback()
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    _write_fallback(data)
