"""Windows 11 활성 창·유휴·잠금 감지 (ctypes Win32 API)."""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from functools import lru_cache

import psutil

from .base import WindowInfo

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
kernel32.GetTickCount.restype = wintypes.DWORD

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]

version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]

DESKTOP_SWITCHDESKTOP = 0x0100
UWP_HOST = "applicationframehost.exe"
LOCK_APPS = {"lockapp.exe", "logonui.exe"}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _window_text(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _uwp_child_pid(hwnd, host_pid: int) -> int:
    """UWP 앱은 ApplicationFrameHost.exe 가 창을 감싸므로 자식 창에서 실제 프로세스를 찾는다."""
    found = [0]

    @WNDENUMPROC
    def callback(child, _lparam):
        pid = _window_pid(child)
        if pid and pid != host_pid:
            found[0] = pid
            return False
        return True

    user32.EnumChildWindows(hwnd, callback, 0)
    return found[0]


@lru_cache(maxsize=512)
def file_description(exe_path: str) -> str:
    """exe 의 버전 정보에서 FileDescription(예: 'Microsoft Excel')을 읽는다."""
    if not exe_path:
        return ""
    try:
        size = version.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return ""
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(exe_path, 0, size, data):
            return ""
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) or not length.value:
            return ""
        lang, codepage = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
        key = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
        if not version.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(length)) or not length.value:
            return ""
        return ctypes.wstring_at(ptr, length.value - 1).strip()
    except OSError:
        return ""


class WindowsProbe:
    def foreground_window(self) -> WindowInfo | None:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        title = _window_text(hwnd)
        pid = _window_pid(hwnd)
        try:
            proc = psutil.Process(pid)
            exe_name = proc.name()
            if exe_name.lower() == UWP_HOST:
                child = _uwp_child_pid(hwnd, pid)
                if child:
                    pid = child
                    proc = psutil.Process(pid)
                    exe_name = proc.name()
            try:
                exe_path = proc.exe()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                exe_path = ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return WindowInfo(app_name="알 수 없음", window_title=title, pid=pid)
        label = file_description(exe_path) or os.path.splitext(exe_name)[0]
        return WindowInfo(app_name=label, window_title=title, exe_name=exe_name, exe_path=exe_path, pid=pid)

    def idle_seconds(self) -> float:
        info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        elapsed_ms = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0

    def is_locked(self) -> bool:
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            try:
                if psutil.Process(_window_pid(hwnd)).name().lower() in LOCK_APPS:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        desk = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not desk:
            return True  # 잠금 시에는 보안 데스크톱으로 전환되어 열 수 없다
        try:
            return not user32.SwitchDesktop(desk)
        finally:
            user32.CloseDesktop(desk)
