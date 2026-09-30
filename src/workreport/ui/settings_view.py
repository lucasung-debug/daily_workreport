"""설정 탭."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .. import autostart, credentials
from ..config import DEFAULT_MODEL, CategoryRule, Settings
from ..services import Services
from .common import run_async

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
CLAUDE_MODELS = [DEFAULT_MODEL, "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
STT_ENGINES = [("whisper_local", "로컬 Whisper (오프라인, 기본)"), ("azure", "Azure AI Speech (화자 분리)"), ("clova", "네이버 CLOVA Speech (화자 분리)")]


def _lines(widget: QPlainTextEdit) -> list[str]:
    return [line.strip() for line in widget.toPlainText().splitlines() if line.strip()]


def _secret_field() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setPlaceholderText("변경하려면 입력 (비워 두면 기존 값 유지)")
    return field


class SettingsView(QWidget):
    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services

        # 일반
        self.user_name = QLineEdit()
        self.work_start = QTimeEdit()
        self.work_end = QTimeEdit()
        for w in (self.work_start, self.work_end):
            w.setDisplayFormat("HH:mm")
        self.auto_draft = QCheckBox("퇴근 전에 업무일지 초안 자동 생성")
        self.draft_before = QSpinBox()
        self.draft_before.setRange(0, 180)
        self.draft_before.setSuffix(" 분 전")
        self.autostart = QCheckBox("Windows 시작 시 자동 실행")
        self.autostart.setEnabled(sys.platform == "win32")

        # 활동 수집
        self.outside_hours = QCheckBox("근무시간 외에도 기록 (끄면 출근 30분 전 ~ 퇴근 3시간 후만 기록)")
        self.idle = QSpinBox()
        self.idle.setRange(1, 60)
        self.idle.setSuffix(" 분 입력 없으면 자리 비움")
        self.excluded_apps = QPlainTextEdit()
        self.excluded_apps.setPlaceholderText("KakaoTalk.exe\n카카오톡")
        self.excluded_keywords = QPlainTextEdit()
        self.excluded_keywords.setPlaceholderText("InPrivate\n뱅킹")
        self.categories = QPlainTextEdit()
        self.categories.setPlaceholderText("title | PROJ-\\d+ | PROJ 프로젝트\napp | Excel | 문서 작업")

        # 스크린샷
        self.screenshot = QCheckBox("주기 스크린샷을 Claude 로 분석해 업무 내용 보강 (기본 OFF)")
        self.screenshot_interval = QSpinBox()
        self.screenshot_interval.setRange(1, 120)
        self.screenshot_interval.setSuffix(" 분마다")
        self.keep_images = QCheckBox("분석 후 원본 이미지 보관")

        # 회의
        self.watch = QCheckBox("Windows 녹음기 앱 저장 폴더 감시")
        self.recorder_dir = QLineEdit()
        btn_dir = QPushButton("찾아보기")
        btn_dir.clicked.connect(self._pick_recorder_dir)
        self.detect = QCheckBox("회의 앱에서 마이크가 켜지면 녹음 권유 알림")
        self.meeting_apps = QPlainTextEdit()
        self.mic = QComboBox()
        self.speaker = QComboBox()
        for combo in (self.mic, self.speaker):
            combo.setEditable(True)
        btn_devices = QPushButton("장치 목록 새로 고침")
        btn_devices.clicked.connect(self._load_devices)
        self.split = QCheckBox("내 목소리 / PC 소리를 2채널로 녹음해 '나 / 상대방' 구분")
        self.retention = QSpinBox()
        self.retention.setRange(0, 3650)
        self.retention.setSuffix(" 일 (0 = 계속 보관)")

        # STT
        self.stt_engine = QComboBox()
        for key, label in STT_ENGINES:
            self.stt_engine.addItem(label, key)
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(WHISPER_MODELS)
        self.whisper_model.setEditable(True)
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cpu", "cuda", "auto"])
        self.whisper_compute = QComboBox()
        self.whisper_compute.addItems(["int8", "int8_float16", "float16", "float32"])
        self.whisper_dir = QLineEdit()
        self.whisper_dir.setPlaceholderText("비워 두면 자동 다운로드")
        btn_wdir = QPushButton("찾아보기")
        btn_wdir.clicked.connect(lambda: self._pick_dir(self.whisper_dir))
        self.azure_region = QLineEdit()
        self.azure_endpoint = QLineEdit()
        self.azure_endpoint.setPlaceholderText("https://<리소스>.cognitiveservices.azure.com (비우면 지역 엔드포인트)")
        self.azure_key = _secret_field()
        self.clova_url = QLineEdit()
        self.clova_url.setPlaceholderText("https://clovaspeech-gw.ncloud.com/external/v1/...")
        self.clova_secret = _secret_field()
        self.max_speakers = QSpinBox()
        self.max_speakers.setRange(0, 20)
        self.max_speakers.setSpecialValueText("자동")

        # Claude
        self.claude_key = _secret_field()
        self.claude_status = QLabel()
        self.claude_model = QComboBox()
        self.claude_model.addItems(CLAUDE_MODELS)
        self.claude_model.setEditable(True)
        self.claude_effort = QComboBox()
        self.claude_effort.addItems(EFFORTS)
        self.claude_fallbacks = QCheckBox("거절 시 서버 측 대체 모델로 재시도 (fallbacks)")

        def group(title: str, rows: list) -> QGroupBox:
            box = QGroupBox(title)
            form = QFormLayout(box)
            for row in rows:
                if isinstance(row, tuple):
                    form.addRow(*row)
                else:
                    form.addRow(row)
            return box

        def with_button(widget, button) -> QWidget:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(widget, 1)
            h.addWidget(button)
            return w

        content = QWidget()
        v = QVBoxLayout(content)
        v.addWidget(group("일반", [("내 이름", self.user_name), ("출근 시각", self.work_start), ("퇴근 시각", self.work_end), self.auto_draft, ("초안 생성 시각", self.draft_before), self.autostart]))
        v.addWidget(group("활동 기록", [self.outside_hours, ("자리 비움 기준", self.idle), ("제외 앱 (한 줄에 하나)", self.excluded_apps), ("제외 제목 키워드", self.excluded_keywords), ("분류 규칙 (대상 | 정규식 | 분류)", self.categories)]))
        v.addWidget(group("스크린샷 (선택)", [self.screenshot, ("캡처 주기", self.screenshot_interval), self.keep_images]))
        v.addWidget(
            group(
                "회의 녹음",
                [
                    self.watch,
                    ("녹음기 앱 저장 폴더", with_button(self.recorder_dir, btn_dir)),
                    self.detect,
                    ("회의 앱 키워드", self.meeting_apps),
                    ("마이크", self.mic),
                    ("스피커(루프백)", with_button(self.speaker, btn_devices)),
                    self.split,
                    ("앱 녹음 파일 보관", self.retention),
                ],
            )
        )
        v.addWidget(
            group(
                "음성 인식 (STT) — 녹음이 끝난 파일을 변환",
                [
                    ("엔진", self.stt_engine),
                    ("Whisper 모델", self.whisper_model),
                    ("Whisper 장치", self.whisper_device),
                    ("Whisper 연산 정밀도", self.whisper_compute),
                    ("Whisper 모델 폴더", with_button(self.whisper_dir, btn_wdir)),
                    ("Azure 지역", self.azure_region),
                    ("Azure 엔드포인트", self.azure_endpoint),
                    ("Azure Speech 키", self.azure_key),
                    ("CLOVA Invoke URL", self.clova_url),
                    ("CLOVA Secret Key", self.clova_secret),
                    ("최대 화자 수", self.max_speakers),
                ],
            )
        )
        v.addWidget(group("Claude (AI 요약)", [("API 키", self.claude_key), self.claude_status, ("모델", self.claude_model), ("추론 강도(effort)", self.claude_effort), self.claude_fallbacks]))
        v.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        btn_save = QPushButton("설정 저장")
        btn_save.clicked.connect(self.save)
        btn_reload = QPushButton("되돌리기")
        btn_reload.clicked.connect(self.load)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(btn_reload)
        buttons.addWidget(btn_save)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)
        self.load()

    # ------------------------------------------------------------
    def load(self) -> None:
        s = self.services.store.get()
        self.user_name.setText(s.user_name)
        self.work_start.setTime(QTime(s.work_start_time().hour, s.work_start_time().minute))
        self.work_end.setTime(QTime(s.work_end_time().hour, s.work_end_time().minute))
        self.auto_draft.setChecked(s.auto_draft_enabled)
        self.draft_before.setValue(s.draft_minutes_before_end)
        self.autostart.setChecked(autostart.is_enabled() if sys.platform == "win32" else s.autostart)
        self.outside_hours.setChecked(s.track_outside_work_hours)
        self.idle.setValue(max(1, s.idle_threshold_sec // 60))
        self.excluded_apps.setPlainText("\n".join(s.excluded_apps))
        self.excluded_keywords.setPlainText("\n".join(s.excluded_title_keywords))
        self.categories.setPlainText("\n".join(f"{r.field} | {r.pattern} | {r.category}" for r in s.category_rules))
        self.screenshot.setChecked(s.screenshot_enabled)
        self.screenshot_interval.setValue(s.screenshot_interval_min)
        self.keep_images.setChecked(s.keep_screenshot_images)
        self.watch.setChecked(s.recorder_watch_enabled)
        self.recorder_dir.setText(s.recorder_dir)
        self.recorder_dir.setPlaceholderText(str(s.recorder_path()))
        self.detect.setChecked(s.meeting_detect_enabled)
        self.meeting_apps.setPlainText("\n".join(s.meeting_apps))
        self.mic.setEditText(s.record_mic_device)
        self.speaker.setEditText(s.record_speaker_device)
        for combo in (self.mic, self.speaker):
            combo.lineEdit().setPlaceholderText("비워 두면 Windows 기본 장치")
        self.split.setChecked(s.record_split_channels)
        self.retention.setValue(s.audio_retention_days)
        self.stt_engine.setCurrentIndex(max(0, self.stt_engine.findData(s.stt_engine)))
        self.whisper_model.setCurrentText(s.whisper_model)
        self.whisper_device.setCurrentText(s.whisper_device)
        self.whisper_compute.setCurrentText(s.whisper_compute_type)
        self.whisper_dir.setText(s.whisper_model_dir)
        self.azure_region.setText(s.azure_region)
        self.azure_endpoint.setText(s.azure_endpoint)
        self.clova_url.setText(s.clova_invoke_url)
        self.max_speakers.setValue(s.max_speakers)
        self.claude_model.setCurrentText(s.claude_model)
        self.claude_effort.setCurrentText(s.claude_effort)
        self.claude_fallbacks.setChecked(s.claude_fallbacks)
        for field in (self.azure_key, self.clova_secret, self.claude_key):
            field.clear()
        self._update_claude_status()

    def _update_claude_status(self) -> None:
        ok = self.services.claude.available()
        self.claude_status.setText("✅ API 키 설정됨" if ok else "⚠ API 키가 없으면 통계 기반 초안만 만들고, 회의록 요약은 건너뜁니다.")

    @staticmethod
    def _parse_rules(text: str) -> list[CategoryRule]:
        rules = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 3 and parts[0] in ("app", "title") and parts[1] and parts[2]:
                rules.append(CategoryRule(field=parts[0], pattern=parts[1], category=parts[2]))
        return rules

    def collect(self) -> Settings:
        s = self.services.store.get().model_copy(deep=True)
        s.user_name = self.user_name.text().strip()
        s.work_start = self.work_start.time().toString("HH:mm")
        s.work_end = self.work_end.time().toString("HH:mm")
        s.auto_draft_enabled = self.auto_draft.isChecked()
        s.draft_minutes_before_end = self.draft_before.value()
        s.autostart = self.autostart.isChecked()
        s.track_outside_work_hours = self.outside_hours.isChecked()
        s.idle_threshold_sec = self.idle.value() * 60
        s.excluded_apps = _lines(self.excluded_apps)
        s.excluded_title_keywords = _lines(self.excluded_keywords)
        s.category_rules = self._parse_rules(self.categories.toPlainText())
        s.screenshot_enabled = self.screenshot.isChecked()
        s.screenshot_interval_min = self.screenshot_interval.value()
        s.keep_screenshot_images = self.keep_images.isChecked()
        s.recorder_watch_enabled = self.watch.isChecked()
        s.recorder_dir = self.recorder_dir.text().strip()
        s.meeting_detect_enabled = self.detect.isChecked()
        s.meeting_apps = _lines(self.meeting_apps)
        s.record_mic_device = self.mic.currentText().strip()
        s.record_speaker_device = self.speaker.currentText().strip()
        s.record_split_channels = self.split.isChecked()
        s.audio_retention_days = self.retention.value()
        s.stt_engine = self.stt_engine.currentData()
        s.whisper_model = self.whisper_model.currentText().strip() or "small"
        s.whisper_device = self.whisper_device.currentText()
        s.whisper_compute_type = self.whisper_compute.currentText()
        s.whisper_model_dir = self.whisper_dir.text().strip()
        s.azure_region = self.azure_region.text().strip()
        s.azure_endpoint = self.azure_endpoint.text().strip()
        s.clova_invoke_url = self.clova_url.text().strip()
        s.max_speakers = self.max_speakers.value()
        s.claude_model = self.claude_model.currentText().strip() or DEFAULT_MODEL
        s.claude_effort = self.claude_effort.currentText()
        s.claude_fallbacks = self.claude_fallbacks.isChecked()
        return s

    def save(self) -> None:
        new = self.collect()
        for field, name in ((self.claude_key, credentials.ANTHROPIC_API_KEY), (self.azure_key, credentials.AZURE_SPEECH_KEY), (self.clova_secret, credentials.CLOVA_SECRET)):
            if field.text().strip():
                credentials.set_secret(name, field.text().strip())
        if sys.platform == "win32" and new.autostart != autostart.is_enabled():
            try:
                autostart.set_enabled(new.autostart)
            except OSError as exc:
                QMessageBox.warning(self, "자동 실행", f"시작 프로그램 등록에 실패했습니다: {exc}")
        self.services.store.update(new)
        self.load()
        QMessageBox.information(self, "설정", "설정을 저장했습니다.")

    # ------------------------------------------------------------
    def _pick_dir(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "폴더 선택", target.text() or target.placeholderText())
        if path:
            target.setText(path)

    def _pick_recorder_dir(self) -> None:
        self._pick_dir(self.recorder_dir)

    def _load_devices(self) -> None:
        backend = self.services.recorder.backend
        if not hasattr(backend, "list_devices"):
            return

        def fill(result) -> None:
            mics, speakers = result
            for combo, names in ((self.mic, mics), (self.speaker, speakers)):
                current = combo.currentText()
                combo.clear()
                combo.addItem("")
                combo.addItems(names)
                combo.setEditText(current)

        run_async(backend.list_devices, on_done=fill, on_error=lambda e: QMessageBox.warning(self, "장치 목록", e))
