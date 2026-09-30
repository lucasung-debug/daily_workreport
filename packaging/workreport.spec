# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 설정. scripts/build_exe.ps1 에서 호출한다.
#   WORKREPORT_ONEDIR=1 이면 폴더형(시작이 빠름), 아니면 단일 exe.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parent
ONEDIR = os.environ.get("WORKREPORT_ONEDIR") == "1"
ICON = HERE / "workreport.ico"

hiddenimports = collect_submodules("workreport") + [
    "keyring.backends.Windows",
    "win32ctypes.core",
    "soundcard.mediafoundation",
]
datas = []
binaries = []

try:  # 로컬 Whisper (선택 설치)
    import faster_whisper  # noqa: F401

    datas += collect_data_files("faster_whisper")  # silero VAD 모델
    binaries += collect_dynamic_libs("ctranslate2")
    hiddenimports += collect_submodules("faster_whisper") + ["ctranslate2"]
except ImportError:
    pass

excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtTest",
    "tkinter", "matplotlib", "pytest",
]

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_kwargs = dict(
    name="WorkReport",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON) if ICON.exists() else None,
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_kwargs)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="WorkReport")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **exe_kwargs)
