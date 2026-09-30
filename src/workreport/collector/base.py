"""플랫폼별 활성 창·유휴 상태 조회 인터페이스."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WindowInfo:
    app_name: str  # 사람이 읽는 앱 이름 (예: "Microsoft Excel"), 없으면 exe 이름
    window_title: str
    exe_name: str = ""  # 예: "EXCEL.EXE"
    exe_path: str = ""
    pid: int = 0


class PlatformProbe(Protocol):
    def foreground_window(self) -> WindowInfo | None:
        """전면 창. 없으면(바탕 화면 등) None."""

    def idle_seconds(self) -> float:
        """마지막 키보드·마우스 입력 이후 경과 초."""

    def is_locked(self) -> bool:
        """잠금 화면 여부."""


def default_probe() -> PlatformProbe:
    if sys.platform == "win32":
        from .windows import WindowsProbe

        return WindowsProbe()
    from .fake import FakeProbe

    return FakeProbe(WindowInfo("개발 환경", "WorkReport (비 Windows 환경)", "python"))
