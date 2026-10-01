"""설정: 카테고리별 페이지(일반·활동 기록·회의 녹음·음성 인식·AI·개인정보), 토글, 엔진 선택 카드, 연결 테스트, 저장 바."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QTime, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import autostart, credentials, paths
from ..config import DEFAULT_MODEL, CategoryRule, Settings
from ..services import Services
from .common import run_async
from .icons import bind_icon
from .theme import repolish
from .widgets import (
    discard,
    Card,
    Chip,
    Divider,
    IconBadge,
    PageHeader,
    SegmentedControl,
    ToggleSwitch,
    button,
    hbox,
    icon_button,
    make_label,
    scroll_area,
    vbox,
)

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
CLAUDE_MODELS = [DEFAULT_MODEL, "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"]
EFFORTS = [("low", "낮음"), ("medium", "보통"), ("high", "높음"), ("xhigh", "매우 높음"), ("max", "최대")]
ENGINES = [
    ("whisper_local", "로컬 Whisper", "이 PC 에서 변환해요. 음성이 밖으로 나가지 않아요. CPU 에서는 시간이 좀 걸려요.", "lock"),
    ("azure", "Azure AI Speech", "빠르고 화자를 구분해요. Azure Speech 리소스 키가 필요해요.", "wave"),
    ("clova", "네이버 CLOVA Speech", "한국어 인식률과 화자 구분이 좋아요. CLOVA Speech 키가 필요해요.", "users"),
]
PAGES = [
    ("general", "일반", "settings"),
    ("activity", "활동 기록", "activity"),
    ("meeting", "회의 녹음", "mic"),
    ("stt", "음성 인식", "wave"),
    ("ai", "AI (Claude)", "sparkles"),
    ("privacy", "개인정보·데이터", "shield"),
]


def _open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class SettingsCard(Card):
    """제목·설명·컨트롤이 한 줄씩 들어가는 설정 카드."""

    def __init__(self, title: str, caption: str = ""):
        super().__init__(title, caption)
        self.body.setSpacing(0)
        self._rows = 0

    def _sep(self) -> None:
        if self._rows:
            self.body.addWidget(Divider())
        self._rows += 1

    def add_row(self, title: str, description: str, control: QWidget | None) -> None:
        self._sep()
        texts = vbox(make_label(title), spacing=1)
        texts.itemAt(0).widget().setStyleSheet("font-weight: 500;")
        if description:
            texts.addWidget(make_label(description, "caption", wrap=True))
        row = hbox(texts, control, spacing=24, margins=(0, 12, 0, 12)) if control else hbox(texts, margins=(0, 12, 0, 12))
        row.setStretch(0, 1)
        self.body.addLayout(row)

    def add_block(self, title: str, description: str, control: QWidget) -> None:
        self._sep()
        head = make_label(title)
        head.setStyleSheet("font-weight: 500;")
        block = vbox(head, spacing=6, margins=(0, 12, 0, 12))
        if description:
            block.addWidget(make_label(description, "caption", wrap=True))
        if isinstance(control, QLayout):
            block.addLayout(control)
        else:
            block.addWidget(control)
        self.body.addLayout(block)


class ChoiceCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, description: str, icon_name: str):
        super().__init__()
        self.key = key
        self.setObjectName("ChoiceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)
        title_label = make_label(title)
        title_label.setStyleSheet("font-weight: 600;")
        self.setLayout(hbox(IconBadge(icon_name, "accent", 34), vbox(title_label, make_label(description, "caption", wrap=True), spacing=2), spacing=12, margins=(14, 12, 14, 12)))

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        repolish(self)

    def mousePressEvent(self, _event) -> None:
        self.clicked.emit(self.key)


class RuleEditor(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[QWidget, QComboBox, QLineEdit, QLineEdit]] = []
        self.box = QVBoxLayout()
        self.box.setSpacing(6)
        self.empty = make_label("규칙이 없어요. 예) 제목에 ‘PROJ-123’ 이 있으면 ‘PROJ 프로젝트’ 로 분류", "caption")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.empty)
        layout.addLayout(self.box)
        layout.addLayout(hbox(button("규칙 추가", "ghost", "plus", on_click=lambda: self.add_rule(CategoryRule(field="title", pattern="", category=""))), None))

    def add_rule(self, rule: CategoryRule) -> None:
        field = QComboBox()
        field.addItem("창 제목", "title")
        field.addItem("앱 이름", "app")
        field.setCurrentIndex(0 if rule.field == "title" else 1)
        field.setFixedWidth(110)
        pattern = QLineEdit(rule.pattern)
        pattern.setPlaceholderText("정규식 (예: PROJ-\\d+)")
        category = QLineEdit(rule.category)
        category.setPlaceholderText("분류 이름")
        category.setFixedWidth(160)
        row = QWidget()
        delete = icon_button("x", "규칙 삭제", lambda: self._remove(row), "text3", 16)
        row.setLayout(hbox(field, pattern, category, delete, spacing=6))
        for w in (pattern, category):
            w.textEdited.connect(self.changed.emit)
        field.currentIndexChanged.connect(self.changed.emit)
        self.rows.append((row, field, pattern, category))
        self.box.addWidget(row)
        self.empty.setVisible(False)
        self.changed.emit()

    def _remove(self, row: QWidget) -> None:
        self.rows = [r for r in self.rows if r[0] is not row]
        discard(row)
        self.empty.setVisible(not self.rows)
        self.changed.emit()

    def set_rules(self, rules: list[CategoryRule]) -> None:
        for row, *_ in self.rows:
            discard(row)
        self.rows = []
        for rule in rules:
            self.add_rule(rule)
        self.empty.setVisible(not self.rows)

    def rules(self) -> list[CategoryRule]:
        out = []
        for _row, field, pattern, category in self.rows:
            if pattern.text().strip() and category.text().strip():
                out.append(CategoryRule(field=field.currentData(), pattern=pattern.text().strip(), category=category.text().strip()))
        return out


def _lines(widget: QPlainTextEdit) -> list[str]:
    return [line.strip() for line in widget.toPlainText().splitlines() if line.strip()]


def _text_area(placeholder: str, height: int = 86) -> QPlainTextEdit:
    area = QPlainTextEdit()
    area.setPlaceholderText(placeholder)
    area.setFixedHeight(height)
    return area


def _spin(minimum: int, maximum: int, suffix: str, width: int = 120) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSuffix(suffix)
    spin.setButtonSymbols(QSpinBox.NoButtons)
    spin.setFixedWidth(width)
    spin.setAlignment(Qt.AlignRight)
    return spin


class SecretField(QLineEdit):
    """비밀번호처럼 가려지는 입력칸 + 오른쪽 눈 아이콘(보기/숨기기)."""

    def __init__(self):
        super().__init__()
        self.setEchoMode(QLineEdit.Password)
        self.setPlaceholderText("변경할 때만 입력하세요")
        self.setMinimumWidth(320)
        self.setTextMargins(0, 0, 26, 0)
        self.eye = QToolButton(self)
        self.eye.setCursor(Qt.PointingHandCursor)
        self.eye.setCheckable(True)
        self.eye.setToolTip("입력한 키 보기")
        bind_icon(self.eye, "eye", "text3", 16)
        self.eye.toggled.connect(lambda on: self.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.eye.resize(24, 24)
        self.eye.move(self.width() - 30, (self.height() - 24) // 2)


class SettingsView(QWidget):
    status_message = Signal(str, str)
    theme_changed = Signal(str)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self._loading = False
        self._dirty = False

        header = PageHeader("설정", "기록 방식·회의 녹음·AI 연결을 바꿀 수 있어요")

        # 왼쪽 카테고리
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QToolButton] = {}
        nav = vbox(spacing=2)
        for key, label, icon_name in PAGES:
            btn = QToolButton()
            btn.setObjectName("NavButton")
            btn.setText(f"  {label}")
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setMinimumHeight(38)
            btn.setMinimumWidth(190)
            btn.setCursor(Qt.PointingHandCursor)
            bind_icon(btn, icon_name, "text2", 17, on_role="accent_text")
            btn.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            nav.addWidget(btn)
        nav.addStretch(1)
        nav_widget = QWidget()
        nav_widget.setFixedWidth(210)
        nav_widget.setLayout(nav)

        self.pages = QStackedWidget()
        self.page_index: dict[str, int] = {}
        for key, builder in (
            ("general", self._page_general),
            ("activity", self._page_activity),
            ("meeting", self._page_meeting),
            ("stt", self._page_stt),
            ("ai", self._page_ai),
            ("privacy", self._page_privacy),
        ):
            body = QWidget()
            body.setLayout(vbox(*builder(), None, spacing=14, margins=(0, 0, 8, 24)))
            self.page_index[key] = self.pages.addWidget(scroll_area(body))

        # 저장 바
        self.save_bar = QFrame()
        self.save_bar.setObjectName("AccentCard")
        self.save_bar.setLayout(
            hbox(
                IconBadge("info", "accent", 30),
                make_label("저장하지 않은 변경 사항이 있어요", None),
                None,
                button("되돌리기", "ghost", on_click=self.load),
                button("저장", "primary", "check", on_click=self.save),
                spacing=10,
                margins=(14, 10, 14, 10),
            )
        )
        self.save_bar.hide()

        content = QVBoxLayout()
        content.setSpacing(12)
        content.addWidget(self.pages, 1)
        content.addWidget(self.save_bar)
        body = hbox(nav_widget, content, spacing=24)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(20)
        layout.addWidget(header)
        layout.addLayout(body, 1)

        self._wire_dirty()
        self.load()
        self.show_page("general")

    # ------------------------------------------------------------ 페이지
    def _page_general(self) -> list[QWidget]:
        self.user_name = QLineEdit()
        self.user_name.setPlaceholderText("예) 홍길동")
        self.user_name.setMinimumWidth(240)
        profile = SettingsCard("내 정보")
        profile.add_row("이름", "회의록 액션아이템에서 내 일을 찾고 보고서 작성자로 써요.", self.user_name)

        self.work_start = QTimeEdit()
        self.work_end = QTimeEdit()
        for w in (self.work_start, self.work_end):
            w.setDisplayFormat("HH:mm")
            w.setButtonSymbols(QTimeEdit.NoButtons)
            w.setFixedWidth(90)
            w.setAlignment(Qt.AlignCenter)
        self.auto_draft = ToggleSwitch()
        self.draft_before = _spin(0, 180, " 분 전", 110)
        work = SettingsCard("근무 시간")
        work.add_row("출근 · 퇴근", "근무시간 밖의 활동은 기본적으로 기록하지 않아요.", hbox(self.work_start, make_label("–", "muted"), self.work_end, spacing=8))
        work.add_row("업무일지 초안 자동 생성", "퇴근 전에 오늘 초안을 만들어 알림으로 알려 드려요. 이미 쓴 일지는 건드리지 않아요.", self.auto_draft)
        work.add_row("초안 생성 시각", "퇴근 시각 기준으로 언제 만들지 정해요.", self.draft_before)

        self.theme = SegmentedControl([("system", "시스템"), ("light", "라이트"), ("dark", "다크")])
        self.autostart = ToggleSwitch()
        self.autostart.setEnabled(sys.platform == "win32")
        app_card = SettingsCard("앱")
        app_card.add_row("테마", "Windows 설정을 따르거나 직접 고를 수 있어요.", self.theme)
        app_card.add_row("Windows 시작 시 자동 실행", "로그인하면 트레이에서 조용히 기록을 시작해요.", self.autostart)
        return [profile, work, app_card]

    def _page_activity(self) -> list[QWidget]:
        self.outside_hours = ToggleSwitch()
        self.idle = _spin(1, 60, " 분", 90)
        rec = SettingsCard("기록 범위")
        rec.add_row("근무시간 외에도 기록", "끄면 출근 30분 전부터 퇴근 3시간 후까지만 기록해요.", self.outside_hours)
        rec.add_row("자리 비움 기준", "이 시간 동안 키보드·마우스 입력이 없으면 자리 비움으로 기록해요.", self.idle)

        self.excluded_apps = _text_area("KakaoTalk.exe\n카카오톡")
        self.excluded_keywords = _text_area("InPrivate\n뱅킹")
        privacy = SettingsCard("제외 목록", "창 제목을 [제외됨] 으로 가려요")
        privacy.add_block("제외할 앱", "한 줄에 하나씩, 실행 파일 이름이나 앱 이름", self.excluded_apps)
        privacy.add_block("제외할 제목 키워드", "창 제목에 이 단어가 있으면 가려요", self.excluded_keywords)

        self.rules = RuleEditor()
        rules = SettingsCard("분류 규칙", "업무일지 항목을 프로젝트별로 묶을 때 써요")
        rules.add_block("규칙", "", self.rules)

        self.screenshot = ToggleSwitch()
        self.screenshot_interval = _spin(1, 120, " 분마다", 110)
        self.keep_images = ToggleSwitch()
        shots = SettingsCard("스크린샷 분석", "선택 기능")
        shots.add_row("주기 스크린샷으로 업무 내용 보강", "화면을 Claude 로 보내 한 문장 설명만 저장해요. 제외 목록 창은 찍지 않아요.", self.screenshot)
        shots.add_row("캡처 주기", "", self.screenshot_interval)
        shots.add_row("원본 이미지 보관", "끄면 설명을 만든 뒤 바로 지워요.", self.keep_images)
        return [rec, privacy, rules, shots]

    def _page_meeting(self) -> list[QWidget]:
        self.watch = ToggleSwitch()
        self.recorder_dir = QLineEdit()
        self.recorder_dir.setMinimumWidth(300)
        recorder = SettingsCard("Windows 녹음기 앱 연동")
        recorder.add_row("녹음기 앱 저장 폴더 감시", "녹음기 앱에서 녹음을 저장하면 자동으로 회의록을 만들어요.", self.watch)
        recorder.add_block(
            "저장 폴더",
            "녹음기 앱의 ⋯ → 설정 → 녹음 위치와 같아야 해요. 비워 두면 문서\\Sound Recordings 를 써요.",
            vbox(hbox(self.recorder_dir, button("찾아보기", "secondary", "folder", on_click=lambda: self._pick_dir(self.recorder_dir)), spacing=6)),
        )

        self.mic = QComboBox()
        self.speaker = QComboBox()
        for combo in (self.mic, self.speaker):
            combo.setEditable(True)
            combo.setMinimumWidth(280)
            combo.lineEdit().setPlaceholderText("Windows 기본 장치")
        self.split = ToggleSwitch()
        self.retention = _spin(0, 3650, " 일", 100)
        self.retention.setSpecialValueText("계속 보관")
        app_rec = SettingsCard("앱 녹음", "마이크와 PC 소리를 함께 녹음해요")
        app_rec.add_row("마이크", "", self.mic)
        app_rec.add_row("스피커 (PC 소리)", "온라인 회의 상대방 목소리를 녹음할 출력 장치", hbox(self.speaker, icon_button("refresh", "장치 목록 새로 고침", self._load_devices), spacing=4))
        app_rec.add_row("나 / 상대방 구분", "내 목소리와 PC 소리를 따로 녹음해 전사문에 화자를 표시해요.", self.split)
        app_rec.add_row("녹음 파일 보관", "앱이 만든 녹음 파일만 지워요. 녹음기 앱 파일은 건드리지 않아요.", self.retention)

        self.detect = ToggleSwitch()
        self.meeting_apps = _text_area("Teams\nZoom", 80)
        detect = SettingsCard("회의 감지")
        detect.add_row("회의가 시작되면 녹음 권유", "회의 앱에서 마이크가 켜지면 알림을 띄워요.", self.detect)
        detect.add_block("회의 앱 키워드", "한 줄에 하나씩", self.meeting_apps)
        return [recorder, app_rec, detect]

    def _page_stt(self) -> list[QWidget]:
        self.engine_cards: dict[str, ChoiceCard] = {}
        engine_card = SettingsCard("음성 인식 엔진", "녹음이 끝난 파일을 텍스트로 바꿔요")
        choices = QVBoxLayout()
        choices.setSpacing(8)
        for key, title, desc, icon_name in ENGINES:
            card = ChoiceCard(key, title, desc, icon_name)
            card.clicked.connect(self.select_engine)
            self.engine_cards[key] = card
            choices.addWidget(card)
        engine_card.body.addSpacing(8)
        engine_card.body.addLayout(choices)

        self.whisper_model = QComboBox()
        self.whisper_model.addItems(WHISPER_MODELS)
        self.whisper_model.setEditable(True)
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cpu", "cuda", "auto"])
        self.whisper_compute = QComboBox()
        self.whisper_compute.addItems(["int8", "int8_float16", "float16", "float32"])
        self.whisper_dir = QLineEdit()
        self.whisper_dir.setPlaceholderText("비워 두면 자동으로 내려받아요")
        self.whisper_card = SettingsCard("로컬 Whisper")
        self.whisper_card.add_row("모델", "small 이 CPU 에서 무난해요. GPU 가 있으면 large-v3 를 추천해요.", self.whisper_model)
        self.whisper_card.add_row("장치", "", self.whisper_device)
        self.whisper_card.add_row("연산 정밀도", "CPU 는 int8 이 빨라요.", self.whisper_compute)
        self.whisper_card.add_block(
            "모델 폴더",
            "사내망에서 내려받기가 막히면 Hugging Face 의 Systran/faster-whisper-small 파일을 받아 이 폴더를 지정하세요.",
            vbox(hbox(self.whisper_dir, button("찾아보기", "secondary", "folder", on_click=lambda: self._pick_dir(self.whisper_dir)), spacing=6)),
        )

        self.azure_region = QLineEdit()
        self.azure_endpoint = QLineEdit()
        self.azure_endpoint.setPlaceholderText("https://<리소스>.cognitiveservices.azure.com")
        self.azure_endpoint.setMinimumWidth(320)
        self.azure_key = SecretField()
        self.azure_card = SettingsCard("Azure AI Speech")
        self.azure_card.add_row("지역", "예) koreacentral", self.azure_region)
        self.azure_card.add_row("엔드포인트", "비워 두면 지역 엔드포인트를 써요.", self.azure_endpoint)
        self.azure_card.add_row("Speech 키", "Windows 자격 증명 관리자에 안전하게 저장돼요.", self.azure_key)

        self.clova_url = QLineEdit()
        self.clova_url.setPlaceholderText("https://clovaspeech-gw.ncloud.com/external/v1/...")
        self.clova_url.setMinimumWidth(320)
        self.clova_secret = SecretField()
        self.clova_card = SettingsCard("네이버 CLOVA Speech")
        self.clova_card.add_row("Invoke URL", "", self.clova_url)
        self.clova_card.add_row("Secret Key", "Windows 자격 증명 관리자에 안전하게 저장돼요.", self.clova_secret)

        self.max_speakers = _spin(0, 20, " 명", 90)
        self.max_speakers.setSpecialValueText("자동")
        common = SettingsCard("화자 구분")
        common.add_row("최대 화자 수", "Azure·CLOVA 화자 분리에 써요.", self.max_speakers)
        return [engine_card, self.whisper_card, self.azure_card, self.clova_card, common]

    def _page_ai(self) -> list[QWidget]:
        self.claude_chip = Chip("", "neutral")
        self.claude_key = SecretField()
        self.btn_test = button("연결 테스트", "secondary", "check-circle", on_click=self.test_claude)
        link = button("API 키 발급하기 →", "link", on_click=lambda: QDesktopServices.openUrl(QUrl("https://console.anthropic.com/settings/keys")))
        conn = SettingsCard("Claude 연결")
        conn.add_row("상태", "키가 없으면 업무일지는 사용 시간 통계로, 회의는 전사문만 저장돼요.", self.claude_chip)
        conn.add_row("API 키", "Windows 자격 증명 관리자에 안전하게 저장돼요.", vbox(self.claude_key, hbox(link, None, self.btn_test), spacing=6))

        self.claude_model = QComboBox()
        self.claude_model.addItems(CLAUDE_MODELS)
        self.claude_model.setEditable(True)
        self.claude_model.setMinimumWidth(220)
        self.claude_effort = SegmentedControl(EFFORTS)
        self.claude_fallbacks = ToggleSwitch()
        model = SettingsCard("모델")
        model.add_row("모델", "기본은 claude-opus-5-5 예요.", self.claude_model)
        model.add_row("추론 강도", "높을수록 꼼꼼하지만 느리고 비용이 늘어요.", self.claude_effort)
        model.add_row("거절 시 대체 모델로 재시도", "드물게 요청이 거절되면 서버에서 다른 모델로 다시 시도해요.", self.claude_fallbacks)
        return [conn, model]

    def _page_privacy(self) -> list[QWidget]:
        info = SettingsCard("무엇이 어디로 가나요?")
        for title, desc in (
            ("이 PC 에만 저장", "활동 기록·업무일지·회의록은 로컬 SQLite DB 에 저장돼요."),
            ("Claude API 로 전송", "업무일지 초안용 활동 요약(앱·창 제목·시간), 회의 전사문, 메모 — API 키를 설정한 경우에만."),
            ("Azure·CLOVA 로 전송", "해당 엔진을 고른 경우에만 회의 오디오를 보내요. 로컬 Whisper 는 전송하지 않아요."),
            ("회의 녹음 동의", "녹음할 때는 반드시 참석자에게 알리고 동의를 받아 주세요."),
        ):
            info.add_row(title, desc, None)
        data = SettingsCard("데이터 위치")
        data.add_row("데이터 폴더", str(paths.app_data_dir()), button("열기", "secondary", "folder", on_click=lambda: _open_path(paths.app_data_dir())))
        data.add_row("로그", str(paths.log_dir()), button("열기", "secondary", "folder", on_click=lambda: _open_path(paths.log_dir())))
        return [info, data]

    # ------------------------------------------------------------ 이동·변경 감지
    def show_page(self, key: str) -> None:
        self.nav_buttons[key].setChecked(True)
        self.pages.setCurrentIndex(self.page_index[key])

    def select_engine(self, key: str) -> None:
        for k, card in self.engine_cards.items():
            card.set_selected(k == key)
        self.whisper_card.setVisible(key == "whisper_local")
        self.azure_card.setVisible(key == "azure")
        self.clova_card.setVisible(key == "clova")
        self._engine = key
        self._mark_dirty()

    def _wire_dirty(self) -> None:
        for w in (self.user_name, self.recorder_dir, self.whisper_dir, self.azure_region, self.azure_endpoint, self.azure_key, self.clova_url, self.clova_secret, self.claude_key):
            w.textEdited.connect(self._mark_dirty)
        for w in (self.excluded_apps, self.excluded_keywords, self.meeting_apps):
            w.textChanged.connect(self._mark_dirty)
        for w in (self.work_start, self.work_end):
            w.timeChanged.connect(self._mark_dirty)
        for w in (self.draft_before, self.idle, self.screenshot_interval, self.retention, self.max_speakers):
            w.valueChanged.connect(self._mark_dirty)
        for w in (self.auto_draft, self.autostart, self.outside_hours, self.screenshot, self.keep_images, self.watch, self.split, self.detect, self.claude_fallbacks):
            w.toggled.connect(self._mark_dirty)
        for w in (self.whisper_model, self.whisper_device, self.whisper_compute, self.claude_model, self.mic, self.speaker):
            w.currentTextChanged.connect(self._mark_dirty)
        self.theme.changed.connect(self._mark_dirty)
        self.claude_effort.changed.connect(self._mark_dirty)
        self.rules.changed.connect(self._mark_dirty)

    def _mark_dirty(self, *_args) -> None:
        if not self._loading:
            self._dirty = True
            self.save_bar.show()

    @property
    def dirty(self) -> bool:
        return self._dirty

    # ------------------------------------------------------------ 불러오기·저장
    def load(self) -> None:
        self._loading = True
        s = self.services.store.get()
        self.user_name.setText(s.user_name)
        self.work_start.setTime(QTime(s.work_start_time().hour, s.work_start_time().minute))
        self.work_end.setTime(QTime(s.work_end_time().hour, s.work_end_time().minute))
        self.auto_draft.setChecked(s.auto_draft_enabled)
        self.draft_before.setValue(s.draft_minutes_before_end)
        self.theme.set_current(s.theme)
        self.autostart.setChecked(autostart.is_enabled() if sys.platform == "win32" else s.autostart)
        self.outside_hours.setChecked(s.track_outside_work_hours)
        self.idle.setValue(max(1, s.idle_threshold_sec // 60))
        self.excluded_apps.setPlainText("\n".join(s.excluded_apps))
        self.excluded_keywords.setPlainText("\n".join(s.excluded_title_keywords))
        self.rules.set_rules(s.category_rules)
        self.screenshot.setChecked(s.screenshot_enabled)
        self.screenshot_interval.setValue(s.screenshot_interval_min)
        self.keep_images.setChecked(s.keep_screenshot_images)
        self.watch.setChecked(s.recorder_watch_enabled)
        self.recorder_dir.setText(s.recorder_dir)
        self.recorder_dir.setPlaceholderText(str(s.recorder_path()))
        self.mic.setEditText(s.record_mic_device)
        self.speaker.setEditText(s.record_speaker_device)
        self.split.setChecked(s.record_split_channels)
        self.retention.setValue(s.audio_retention_days)
        self.detect.setChecked(s.meeting_detect_enabled)
        self.meeting_apps.setPlainText("\n".join(s.meeting_apps))
        self.select_engine(s.stt_engine)
        self.whisper_model.setCurrentText(s.whisper_model)
        self.whisper_device.setCurrentText(s.whisper_device)
        self.whisper_compute.setCurrentText(s.whisper_compute_type)
        self.whisper_dir.setText(s.whisper_model_dir)
        self.azure_region.setText(s.azure_region)
        self.azure_endpoint.setText(s.azure_endpoint)
        self.clova_url.setText(s.clova_invoke_url)
        self.max_speakers.setValue(s.max_speakers)
        self.claude_model.setCurrentText(s.claude_model)
        self.claude_effort.set_current(s.claude_effort)
        self.claude_fallbacks.setChecked(s.claude_fallbacks)
        for field in (self.azure_key, self.clova_secret, self.claude_key):
            field.clear()
        self._update_claude_status()
        self._loading = False
        self._dirty = False
        self.save_bar.hide()

    def _update_claude_status(self) -> None:
        if self.services.claude.available():
            self.claude_chip.set_kind("success", "키 설정됨")
        else:
            self.claude_chip.set_kind("warning", "키 없음")

    def collect(self) -> Settings:
        s = self.services.store.get().model_copy(deep=True)
        s.user_name = self.user_name.text().strip()
        s.work_start = self.work_start.time().toString("HH:mm")
        s.work_end = self.work_end.time().toString("HH:mm")
        s.auto_draft_enabled = self.auto_draft.isChecked()
        s.draft_minutes_before_end = self.draft_before.value()
        s.theme = self.theme.current() or "system"
        s.autostart = self.autostart.isChecked()
        s.track_outside_work_hours = self.outside_hours.isChecked()
        s.idle_threshold_sec = self.idle.value() * 60
        s.excluded_apps = _lines(self.excluded_apps)
        s.excluded_title_keywords = _lines(self.excluded_keywords)
        s.category_rules = self.rules.rules()
        s.screenshot_enabled = self.screenshot.isChecked()
        s.screenshot_interval_min = self.screenshot_interval.value()
        s.keep_screenshot_images = self.keep_images.isChecked()
        s.recorder_watch_enabled = self.watch.isChecked()
        s.recorder_dir = self.recorder_dir.text().strip()
        s.record_mic_device = self.mic.currentText().strip()
        s.record_speaker_device = self.speaker.currentText().strip()
        s.record_split_channels = self.split.isChecked()
        s.audio_retention_days = self.retention.value()
        s.meeting_detect_enabled = self.detect.isChecked()
        s.meeting_apps = _lines(self.meeting_apps)
        s.stt_engine = getattr(self, "_engine", s.stt_engine)
        s.whisper_model = self.whisper_model.currentText().strip() or "small"
        s.whisper_device = self.whisper_device.currentText()
        s.whisper_compute_type = self.whisper_compute.currentText()
        s.whisper_model_dir = self.whisper_dir.text().strip()
        s.azure_region = self.azure_region.text().strip()
        s.azure_endpoint = self.azure_endpoint.text().strip()
        s.clova_invoke_url = self.clova_url.text().strip()
        s.max_speakers = self.max_speakers.value()
        s.claude_model = self.claude_model.currentText().strip() or DEFAULT_MODEL
        s.claude_effort = self.claude_effort.current() or "medium"
        s.claude_fallbacks = self.claude_fallbacks.isChecked()
        return s

    def _save_secrets(self) -> None:
        for field, name in ((self.claude_key, credentials.ANTHROPIC_API_KEY), (self.azure_key, credentials.AZURE_SPEECH_KEY), (self.clova_secret, credentials.CLOVA_SECRET)):
            if field.text().strip():
                credentials.set_secret(name, field.text().strip())

    def save(self) -> None:
        new = self.collect()
        old_theme = self.services.store.get().theme
        self._save_secrets()
        if sys.platform == "win32" and new.autostart != autostart.is_enabled():
            try:
                autostart.set_enabled(new.autostart)
            except OSError as exc:
                self.status_message.emit(f"시작 프로그램 등록에 실패했어요: {exc}", "error")
        self.services.store.update(new)
        self.load()
        if new.theme != old_theme:
            self.theme_changed.emit(new.theme)
        self.status_message.emit("설정을 저장했어요.", "success")

    # ------------------------------------------------------------ 기타
    def test_claude(self) -> None:
        if self.claude_key.text().strip():
            credentials.set_secret(credentials.ANTHROPIC_API_KEY, self.claude_key.text().strip())
            self.claude_key.clear()
        if not self.services.claude.available():
            self.status_message.emit("먼저 API 키를 입력하세요.", "error")
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText("확인 중…")

        def ping() -> str:
            return self.services.claude.text("Reply with exactly: OK", "ping", max_tokens=256, effort="low")

        def done(_reply: str) -> None:
            self._reset_test()
            self.claude_chip.set_kind("success", "연결됨")
            self.status_message.emit("Claude 에 연결됐어요.", "success")

        def failed(message: str) -> None:
            self._reset_test()
            self.claude_chip.set_kind("danger", "연결 실패")
            self.status_message.emit(message, "error")

        run_async(ping, on_done=done, on_error=failed)

    def _reset_test(self) -> None:
        self.btn_test.setEnabled(True)
        self.btn_test.setText("연결 테스트")

    def _pick_dir(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "폴더 선택", target.text() or target.placeholderText())
        if path:
            target.setText(path)
            self._mark_dirty()

    def _load_devices(self) -> None:
        backend = self.services.recorder.backend
        if not hasattr(backend, "list_devices"):
            return

        def fill(result) -> None:
            mics, speakers = result
            for combo, names in ((self.mic, mics), (self.speaker, speakers)):
                current = combo.currentText()
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("")
                combo.addItems(names)
                combo.setEditText(current)
                combo.blockSignals(False)
            self.status_message.emit(f"장치를 불러왔어요: 마이크 {len(mics)}개, 스피커 {len(speakers)}개", "success")

        run_async(backend.list_devices, on_done=fill, on_error=lambda e: self.status_message.emit(f"장치 목록을 읽지 못했어요: {e}", "error"))

