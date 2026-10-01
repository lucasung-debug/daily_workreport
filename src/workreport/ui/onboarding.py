"""첫 실행 마법사: 소개·개인정보 안내 → 내 정보·근무시간 → AI 연결(선택)."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import QDialog, QFrame, QLineEdit, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget

from .. import autostart, credentials
from ..services import SettingsStore
from .main_window import AppLogo
from .settings_view import SecretField
from .theme import on_theme_change, tokens
from .widgets import ColorDot, IconBadge, ToggleSwitch, button, hbox, make_label, vbox


def _feature(icon_name: str, role: str, title: str, text: str) -> QWidget:
    w = QWidget()
    t = make_label(title)
    t.setStyleSheet("font-weight: 600;")
    w.setLayout(hbox(IconBadge(icon_name, role, 38), vbox(t, make_label(text, "muted", wrap=True), spacing=2), spacing=14))
    return w


class OnboardingDialog(QDialog):
    def __init__(self, store: SettingsStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("WorkReport 시작하기")
        self.setModal(True)
        self.setFixedSize(620, 600)

        s = store.get()
        self.steps = QStackedWidget()

        # 1. 소개
        privacy = QFrame()
        privacy.setObjectName("SubtleCard")
        privacy.setLayout(
            hbox(
                IconBadge("shield", "success", 34),
                make_label("기록은 이 PC 에만 저장돼요. 제외 목록(뱅킹·시크릿 창 등)은 제목을 가리고, 트레이에서 언제든 기록을 멈출 수 있어요. 회의를 녹음할 때는 참석자에게 꼭 알려 주세요.", "muted", wrap=True),
                spacing=12,
                margins=(14, 12, 14, 12),
            )
        )
        welcome = QWidget()
        welcome.setLayout(
            vbox(
                AppLogo(48),
                8,
                make_label("업무일지, 이제 자동으로 써 드릴게요", "title"),
                make_label("WorkReport 는 하루 동안의 PC 활동과 회의를 모아 퇴근 전에 보고서 초안을 만들어요.", "subtitle"),
                16,
                _feature("activity", "accent", "활동 자동 기록", "어떤 앱·문서로 얼마나 일했는지 2초마다 조용히 기록해요."),
                _feature("mic", "meeting", "회의록 자동 작성", "녹음기 앱이나 앱 녹음 파일을 텍스트로 바꾸고 요약·결정 사항·할 일을 정리해요."),
                _feature("sparkles", "success", "퇴근 전 업무일지 초안", "금일 실적 · 명일 계획 · 이슈 형식으로 초안을 만들어 알려 드려요."),
                12,
                privacy,
                None,
                spacing=10,
            )
        )

        # 2. 내 정보
        self.name = QLineEdit(s.user_name)
        self.name.setPlaceholderText("예) 홍길동")
        self.work_start = QTimeEdit(QTime(s.work_start_time().hour, s.work_start_time().minute))
        self.work_end = QTimeEdit(QTime(s.work_end_time().hour, s.work_end_time().minute))
        for w in (self.work_start, self.work_end):
            w.setDisplayFormat("HH:mm")
            w.setButtonSymbols(QTimeEdit.NoButtons)
            w.setFixedWidth(100)
            w.setAlignment(Qt.AlignCenter)
        self.autostart = ToggleSwitch(sys.platform == "win32")
        self.autostart.setEnabled(sys.platform == "win32")
        profile = QWidget()
        profile.setLayout(
            vbox(
                make_label("나에 대해 알려 주세요", "title"),
                make_label("보고서 작성자와 초안 생성 시각에 쓰여요.", "subtitle"),
                20,
                make_label("이름"),
                self.name,
                12,
                make_label("근무 시간"),
                hbox(self.work_start, make_label("–", "muted"), self.work_end, None, spacing=8),
                make_label("퇴근 10분 전에 업무일지 초안을 만들어 알려 드려요.", "caption"),
                12,
                hbox(vbox(make_label("Windows 시작 시 자동 실행"), make_label("로그인하면 트레이에서 기록을 시작해요.", "caption"), spacing=1), None, self.autostart),
                None,
                spacing=6,
            )
        )

        # 3. AI 연결
        self.api_key = SecretField()
        self.api_key.setPlaceholderText("sk-ant-… (나중에 설정해도 돼요)")
        ai = QWidget()
        ai.setLayout(
            vbox(
                make_label("AI 연결 (선택)", "title"),
                make_label("Claude API 키를 넣으면 자연스러운 업무일지 초안과 회의록을 받을 수 있어요.", "subtitle"),
                20,
                make_label("Claude API 키"),
                self.api_key,
                make_label("키는 Windows 자격 증명 관리자에 안전하게 저장돼요. 키가 없어도 사용 시간 통계로 초안을 만들어요.", "caption", wrap=True),
                16,
                _feature("lock", "accent", "음성 인식은 이 PC 에서", "회의 음성은 기본으로 로컬 Whisper 로 변환해 밖으로 보내지 않아요. 설정에서 Azure·CLOVA 로 바꿀 수 있어요."),
                None,
                spacing=6,
            )
        )
        for page in (welcome, profile, ai):
            self.steps.addWidget(page)

        self.dots = hbox(spacing=6)
        self._dot_widgets: list[ColorDot] = []
        for _ in range(self.steps.count()):
            dot = ColorDot(tokens().border_strong, 8)
            self._dot_widgets.append(dot)
            self.dots.addWidget(dot)
        self.btn_back = button("이전", "ghost", on_click=lambda: self._go(-1))
        self.btn_next = button("다음", "primary", on_click=lambda: self._go(1))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 24)
        layout.setSpacing(18)
        layout.addWidget(self.steps, 1)
        layout.addLayout(hbox(self.dots, None, self.btn_back, self.btn_next))
        on_theme_change(self, self._update)
        self._update()

    def _update(self) -> None:
        idx = self.steps.currentIndex()
        t = tokens()
        for i, dot in enumerate(self._dot_widgets):
            dot.color = t.accent if i == idx else t.border_strong
            dot.update()
        self.btn_back.setVisible(idx > 0)
        self.btn_next.setText("시작하기" if idx == self.steps.count() - 1 else "다음")

    def _go(self, delta: int) -> None:
        idx = self.steps.currentIndex() + delta
        if idx >= self.steps.count():
            self.finish()
            return
        self.steps.setCurrentIndex(max(0, idx))
        self._update()

    def finish(self) -> None:
        s = self.store.get().model_copy(deep=True)
        s.user_name = self.name.text().strip()
        s.work_start = self.work_start.time().toString("HH:mm")
        s.work_end = self.work_end.time().toString("HH:mm")
        s.autostart = self.autostart.isChecked()
        s.onboarding_done = True
        if self.api_key.text().strip():
            credentials.set_secret(credentials.ANTHROPIC_API_KEY, self.api_key.text().strip())
        if sys.platform == "win32" and s.autostart != autostart.is_enabled():
            try:
                autostart.set_enabled(s.autostart)
            except OSError:
                pass
        self.store.update(s)
        self.accept()

    def reject(self) -> None:
        # 닫아도 다시 묻지 않도록 기본값으로 마친다
        s = self.store.get().model_copy(deep=True)
        s.onboarding_done = True
        self.store.update(s)
        super().reject()
