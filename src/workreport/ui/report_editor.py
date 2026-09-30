"""업무일지 탭: 금일 실적 / 명일 계획 / 이슈 편집, AI 초안, 저장·확정, 복사·내보내기."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDateEdit,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from .. import paths
from ..analysis.aggregate import summarize_day
from ..models import DailyReport
from ..report.render import report_to_markdown, report_to_plaintext
from ..services import Services
from .common import ItemTableEditor, run_async


class ReportEditor(QWidget):
    status_message = Signal(str)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.day = date.today().isoformat()
        self._dirty = False
        self._loading = False
        self._busy = False

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd (ddd)")
        self.date_edit.dateChanged.connect(self._on_date_changed)
        btn_today = QPushButton("오늘")
        btn_today.clicked.connect(lambda: self.date_edit.setDate(QDate.currentDate()))
        self.lbl_state = QLabel()

        self.btn_generate = QPushButton("✨ AI 초안 생성")
        self.btn_generate.clicked.connect(self.generate)
        self.btn_save = QPushButton("저장")
        self.btn_save.clicked.connect(lambda: self.save(final=False))
        self.btn_final = QPushButton("확정")
        self.btn_final.clicked.connect(lambda: self.save(final=True))
        self.btn_copy = QPushButton("평문 복사")
        self.btn_copy.setToolTip("그룹웨어·메신저에 붙여넣기 좋은 형식으로 클립보드에 복사")
        self.btn_copy.clicked.connect(self.copy_plaintext)
        self.btn_export = QPushButton("Markdown 내보내기")
        self.btn_export.clicked.connect(self.export_markdown)

        header = QHBoxLayout()
        header.addWidget(QLabel("날짜"))
        header.addWidget(self.date_edit)
        header.addWidget(btn_today)
        header.addWidget(self.lbl_state)
        header.addStretch()
        for btn in (self.btn_generate, self.btn_save, self.btn_final, self.btn_copy, self.btn_export):
            header.addWidget(btn)

        self.summary = QLineEdit()
        self.summary.setPlaceholderText("오늘 업무 한 줄 요약")
        self.summary.textEdited.connect(self._mark_dirty)

        self.accomplishments = ItemTableEditor(show_time=True)
        self.plans = ItemTableEditor(show_time=False)
        self.issues = ItemTableEditor(show_time=False)
        for editor in (self.accomplishments, self.plans, self.issues):
            editor.changed.connect(self._mark_dirty)

        self.memo = QPlainTextEdit()
        self.memo.setPlaceholderText("비고 (보고서 하단에 그대로 들어갑니다)")
        self.memo.textChanged.connect(self._mark_dirty)

        self.notes = QListWidget()
        btn_note = QPushButton("메모 추가")
        btn_note.clicked.connect(self.add_note)
        btn_note_del = QPushButton("메모 삭제")
        btn_note_del.clicked.connect(self.delete_note)

        left = QVBoxLayout()
        left.addWidget(self.summary)
        for title, editor in (("1. 금일 실적", self.accomplishments), ("2. 명일 계획", self.plans), ("3. 이슈 및 협조 요청", self.issues)):
            box = QGroupBox(title)
            QVBoxLayout(box).addWidget(editor)
            left.addWidget(box, 3 if editor is self.accomplishments else 2)
        left_widget = QWidget()
        left_widget.setLayout(left)

        right = QVBoxLayout()
        memo_box = QGroupBox("비고")
        QVBoxLayout(memo_box).addWidget(self.memo)
        notes_box = QGroupBox("오늘 메모 (AI 초안에 반영)")
        notes_layout = QVBoxLayout(notes_box)
        notes_layout.addWidget(self.notes)
        row = QHBoxLayout()
        row.addWidget(btn_note)
        row.addWidget(btn_note_del)
        notes_layout.addLayout(row)
        right.addWidget(memo_box, 1)
        right.addWidget(notes_box, 1)
        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        self.load(self.day)

    # ------------------------------------------------------------ 상태
    def _mark_dirty(self, *_args) -> None:
        if not self._loading:
            self._dirty = True
            self._update_state_label()

    def _update_state_label(self, report: DailyReport | None = None) -> None:
        report = report or self.services.db.get_report(self.day)
        if report is None:
            text = "작성 전"
        else:
            text = "확정됨" if report.status == "final" else "초안"
            text += f" · {datetime.fromtimestamp(report.updated_at):%m-%d %H:%M} 저장"
        if self._dirty:
            text += " · 수정됨"
        self.lbl_state.setText(text)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        for btn in (self.btn_generate, self.btn_save, self.btn_final):
            btn.setEnabled(not busy)
        self.btn_generate.setText("초안 생성 중…" if busy else "✨ AI 초안 생성")
        if message:
            self.status_message.emit(message)

    def confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(self, "저장하지 않은 변경", f"{self.day} 업무일지의 변경 사항을 저장할까요?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            self.save(final=False)
            return True
        return answer == QMessageBox.Discard

    # ------------------------------------------------------------ 로드·저장
    def _on_date_changed(self, qdate: QDate) -> None:
        day = qdate.toString("yyyy-MM-dd")
        if day == self.day:
            return
        if not self.confirm_discard():
            self.date_edit.blockSignals(True)
            self.date_edit.setDate(QDate.fromString(self.day, "yyyy-MM-dd"))
            self.date_edit.blockSignals(False)
            return
        self.load(day)

    def open_date(self, day: str) -> None:
        if day != self.day:
            self.date_edit.setDate(QDate.fromString(day, "yyyy-MM-dd"))  # _on_date_changed 가 불러온다
        elif not self._dirty:
            self.load(day)

    def load(self, day: str) -> None:
        self._loading = True
        self.day = day
        report = self.services.db.get_report(day) or DailyReport(date=day)
        self.summary.setText(report.summary)
        self.accomplishments.set_items(report.accomplishments)
        self.plans.set_items(report.plans)
        self.issues.set_items(report.issues)
        self.memo.setPlainText(report.memo)
        self._load_notes()
        self._loading = False
        self._dirty = False
        self._update_state_label()

    def _load_notes(self) -> None:
        self.notes.clear()
        for note in self.services.db.notes_for(self.day):
            item = QListWidgetItem(f"{datetime.fromtimestamp(note.ts):%H:%M}  {note.text}")
            item.setData(Qt.UserRole, note.id)
            self.notes.addItem(item)

    def current_report(self) -> DailyReport:
        existing = self.services.db.get_report(self.day)
        report = existing or DailyReport(date=self.day)
        report.summary = self.summary.text().strip()
        report.accomplishments = self.accomplishments.items()
        report.plans = self.plans.items()
        report.issues = self.issues.items()
        report.memo = self.memo.toPlainText().strip()
        return report

    def save(self, final: bool = False) -> None:
        report = self.current_report()
        if final:
            report.status = "final"
        self.services.db.save_report(report)
        self._dirty = False
        self._update_state_label(report)
        self.status_message.emit(f"{self.day} 업무일지를 {'확정' if final else '저장'}했습니다.")

    # ------------------------------------------------------------ AI 초안
    def generate(self) -> None:
        if self._busy:
            return
        current = self.current_report()
        if current.accomplishments or current.plans or current.issues:
            answer = QMessageBox.question(self, "AI 초안 생성", "지금 작성된 실적·계획·이슈를 새 초안으로 바꿉니다. 비고는 유지됩니다. 계속할까요?")
            if answer != QMessageBox.Yes:
                return
        if self._dirty:
            self.save(final=False)
        mode = "Claude" if self.services.claude.available() else "통계(Claude API 키 없음)"
        self._set_busy(True, f"{self.day} 업무일지 초안 생성 중… ({mode})")
        day = self.day
        run_async(self.services.generate_report_draft, day, on_done=lambda _r: self._on_generated(day), on_error=self._on_generate_failed)

    def _on_generated(self, day: str) -> None:
        self._set_busy(False, f"{day} 업무일지 초안을 만들었습니다. 내용을 확인하고 수정하세요.")
        if day == self.day:
            self.load(day)

    def _on_generate_failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.warning(self, "초안 생성 실패", message)

    # ------------------------------------------------------------ 메모
    def add_note(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "메모 추가", f"{self.day} 메모")
        if ok and text.strip():
            ts = datetime.now().timestamp() if self.day == date.today().isoformat() else datetime.fromisoformat(f"{self.day}T12:00:00").timestamp()
            self.services.db.add_note(text.strip(), ts)
            self._load_notes()

    def delete_note(self) -> None:
        item = self.notes.currentItem()
        if item:
            self.services.db.delete_note(item.data(Qt.UserRole))
            self._load_notes()

    # ------------------------------------------------------------ 내보내기
    def copy_plaintext(self) -> None:
        QApplication.clipboard().setText(report_to_plaintext(self.current_report()))
        self.status_message.emit("업무일지를 클립보드에 복사했습니다.")

    def markdown(self) -> str:
        settings = self.services.store.get()
        return report_to_markdown(
            self.current_report(),
            summarize_day(self.services.db, self.day),
            author=settings.user_name,
            action_items=self.services.db.open_my_action_items(),
        )

    def export_markdown(self) -> None:
        default = str(paths.documents_dir() / f"업무일지_{self.day}.md")
        path, _ = QFileDialog.getSaveFileName(self, "Markdown 내보내기", default, "Markdown (*.md)")
        if path:
            Path(path).write_text(self.markdown(), encoding="utf-8")
            self.status_message.emit(f"저장했습니다: {path}")
