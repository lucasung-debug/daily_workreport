"""실제 Windows API 스모크 테스트 (GitHub Actions windows-latest 에서 실행)."""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")


def test_windows_probe_calls_succeed():
    from workreport.collector.windows import WindowsProbe, file_description

    probe = WindowsProbe()
    win = probe.foreground_window()  # CI 데스크톱에 창이 없으면 None 일 수 있다
    if win is not None:
        assert isinstance(win.app_name, str) and isinstance(win.window_title, str)
    assert probe.idle_seconds() >= 0
    assert isinstance(probe.is_locked(), bool)
    assert "Python" in file_description(sys.executable) or file_description(sys.executable) == ""


def test_known_folder_documents():
    from workreport import paths

    docs = paths._known_folder(paths._FOLDERID_DOCUMENTS)
    assert docs is not None and docs.is_absolute()
    assert paths.default_sound_recorder_dir().parent == docs


def test_mic_registry_scan_returns_list():
    from workreport.meeting.detect import mic_in_use_apps

    assert isinstance(mic_in_use_apps(), list)


def test_autostart_roundtrip():
    from workreport import autostart

    before = autostart.is_enabled()
    try:
        autostart.set_enabled(True)
        assert autostart.is_enabled()
        autostart.set_enabled(False)
        assert not autostart.is_enabled()
    finally:
        autostart.set_enabled(before)


def test_soundcard_imports():
    pytest.importorskip("soundcard")
    from workreport.meeting.recorder import SoundcardBackend

    backend = SoundcardBackend()
    try:
        mics, speakers = backend.list_devices()  # CI 러너에는 오디오 장치가 없을 수 있다
    except Exception as exc:  # pragma: no cover - 장치 없음
        pytest.skip(f"오디오 장치 없음: {exc}")
    assert isinstance(mics, list) and isinstance(speakers, list)


def test_default_probe_is_windows():
    from workreport.collector.base import default_probe
    from workreport.collector.windows import WindowsProbe

    assert isinstance(default_probe(), WindowsProbe)
    assert Path(sys.executable).exists()
