"""Windows 시작 프로그램 등록 (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run)."""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "WorkReport"


def launch_command() -> str:
    """exe 로 빌드된 경우 exe 경로, 소스 실행이면 pythonw -m workreport."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m workreport --minimized'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    if enabled:
        # 새 사용자 프로필에는 Run 키가 없을 수 있으므로 없으면 만든다
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass  # 키나 값이 없으면 이미 꺼진 상태
