"""업무일지: 날짜 이동, AI 초안, 항목 카드 편집(자동 저장), 확정, 복사·내보내기, 메모·내 할 일."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QLocale, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..analysis.aggregate import summarize_day
from ..models import DailyReport, ReportItem
from ..report.render import format_duration, report_to_markdown, report_to_plaintext
from ..services import Services
from .common import run_async
from .widgets import (
    AutoTextEdit,
    Card,
    Chip,
    ElidedLabel,
    IconBadge,
    button,
    clear_layout,
    discard,
    hbox,
    icon_button,
    make_label,
    scroll_area,
    vbox,
)

AUTOSAVE_MS = 700


# ---------------------------------------------------------------- 항목 카드


class ItemCard(QFrame):
    changed = Signal()
    remove_requested = Signal(object)
    move_requested = Signal(object, int)

    def __init__(self, item: ReportItem, show_time: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("ItemCard")
        self.index_label = make_label("1", "caption")
        self.index_label.setFixedWidth(16)
        self.index_label.setAlignment(Qt.AlignRight | Qt.AlignTop)

        self.title = QLineEdit(item.title)
        self.title.setProperty("flat", True)
        self.title.setProperty("level", "item")
        self.title.setPlaceholderText("업무 제목")
        self.detail = AutoTextEdit("세부 내용 (선택)", muted=True)
        self.detail.setPlainText(item.detail)

        self.minutes = QSpinBox()
        self.minutes.setRange(0, 24 * 60)
        self.minutes.setSingleStep(10)
        self.minutes.setSuffix(" 분")
        self.minutes.setSpecialValueText("시간 –")
        self.minutes.setButtonSymbols(QSpinBox.NoButtons)
        self.minutes.setFixedWidth(84)
        self.minutes.setProperty("flat", True)
        self.minutes.setProperty("tone", "muted")
        self.minutes.setValue(item.time_spent_min or 0)
        self.minutes.setToolTip("투입 시간(분)")
        self.minutes.setVisible(show_time)
        self.category = QLineEdit(item.category)
        self.category.setProperty("flat", True)
        self.category.setPlaceholderText("# 분류")
        self.category.setFixedWidth(150)

        self.btn_up = icon_button("arrow-up", "위로", lambda: self.move_requested.emit(self, -1), "text3", 16)
        self.btn_down = icon_button("arrow-down", "아래로", lambda: self.move_requested.emit(self, 1), "text3", 16)
        self.btn_delete = icon_button("trash", "삭제", lambda: self.remove_requested.emit(self), "text3", 16)

        for w in (self.title, self.category):
            w.textEdited.connect(self.changed.emit)
        self.detail.textChanged.connect(self.changed.emit)
        self.minutes.valueChanged.connect(self.changed.emit)

        meta = hbox(self.minutes, self.category, None, spacing=6)
        center = vbox(self.title, self.detail, meta, spacing=2)
        tools = vbox(hbox(self.btn_up, self.btn_down, self.btn_delete, spacing=0), None)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(8)
        idx_col = vbox(6, self.index_label, None, spacing=0)
        layout.addLayout(idx_col)
        layout.addLayout(center, 1)
        layout.addLayout(tools)

    def item(self) -> ReportItem:
        return ReportItem(
            title=self.title.text().strip(),
            detail=self.detail.toPlainText().strip(),
            time_spent_min=self.minutes.value(),
            category=self.category.text().strip().lstrip("#").strip(),
        )


class ItemList(QWidget):
    """금일 실적 / 명일 계획 / 이슈 항목 카드 목록."""

    changed = Signal()

    def __init__(self, show_time: bool = True, add_label: str = "항목 추가", empty_text: str = "", parent=None):
        super().__init__(parent)
        self.show_time = show_time
        self.cards: list[ItemCard] = []
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(8)
        self.empty = make_label(empty_text, "caption")
        self.empty.setVisible(bool(empty_text))
        self.btn_add = button(add_label, "ghost", "plus", on_click=lambda: self.add_item(focus=True))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.empty)
        layout.addLayout(self.cards_box)
        layout.addLayout(hbox(self.btn_add, None))

    def _renumber(self) -> None:
        for i, card in enumerate(self.cards, 1):
            card.index_label.setText(str(i))
        self.empty.setVisible(not self.cards and bool(self.empty.text()))

    def set_items(self, items: list[ReportItem]) -> None:
        for card in self.cards:
            discard(card)
        self.cards = []
        for item in items:
            self._append(item)
        self._renumber()

    def _append(self, item: ReportItem) -> ItemCard:
        card = ItemCard(item, self.show_time)
        card.changed.connect(self.changed.emit)
        card.remove_requested.connect(self.remove)
        card.move_requested.connect(self.move)
        self.cards.append(card)
        self.cards_box.addWidget(card)
        return card

    def add_item(self, item: ReportItem | None = None, focus: bool = False) -> ItemCard:
        card = self._append(item or ReportItem(title=""))
        self._renumber()
        if focus:
            card.title.setFocus()
        self.changed.emit()
        return card

    def remove(self, card: ItemCard) -> None:
        if card in self.cards:
            self.cards.remove(card)
            discard(card)
            self._renumber()
            self.changed.emit()

    def move(self, card: ItemCard, delta: int) -> None:
        idx = self.cards.index(card)
        target = idx + delta
        if not 0 <= target < len(self.cards):
            return
        self.cards[idx], self.cards[target] = self.cards[target], self.cards[idx]
        self.cards_box.removeWidget(card)
        self.cards_box.insertWidget(target, card)
        self._renumber()
        self.changed.emit()

    def items(self) -> list[ReportItem]:
        return [item for item in (c.item() for c in self.cards) if item.title]

    def count(self) -> int:
        return len(self.cards)


# ---------------------------------------------------------------- 업무일지 화면


class ReportEditor(QWidget):
    status_message = Signal(str, str)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.day = date.today().isoformat()
        self._loading = False
        self._busy = False
        self._last_saved: float | None = None
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(AUTOSAVE_MS)
        self.save_timer.timeout.connect(self._autosave)

        # 헤더
        title = make_label("업무일지", "title")
        self.btn_copy = icon_button("copy", "평문으로 복사 (그룹웨어·메신저에 붙여넣기)", self.copy_plaintext)
        self.btn_export = icon_button("download", "Markdown 파일로 내보내기", self.export_markdown)
        self.btn_final = button("확정", "secondary", "check", on_click=self.toggle_final)
        self.btn_generate = button("AI 초안", "primary", "sparkles", on_click=self.generate)

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy. M. d. (ddd)")
        self.date_edit.setLocale(QLocale(QLocale.Korean, QLocale.SouthKorea))
        self.date_edit.setFixedWidth(150)
        self.date_edit.dateChanged.connect(lambda d: self.open_date(d.toString("yyyy-MM-dd")))
        btn_prev = icon_button("chevron-left", "이전 날", lambda: self._shift(-1))
        btn_next = icon_button("chevron-right", "다음 날", lambda: self._shift(1))
        btn_today = button("오늘", "ghost", on_click=lambda: self.open_date(date.today().isoformat()))
        self.state_chip = Chip("작성 전", "neutral")
        self.saved_label = make_label("", "caption")

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        head = vbox(
            hbox(title, None, self.btn_copy, self.btn_export, 6, self.btn_final, self.btn_generate, spacing=6),
            hbox(btn_prev, self.date_edit, btn_next, btn_today, 10, self.state_chip, self.saved_label, None, spacing=4),
            self.progress,
            spacing=10,
        )

        # 왼쪽: 본문
        self.banner = QFrame()
        self.banner.setObjectName("AccentCard")
        self.banner_text = make_label("", "muted", wrap=True)
        banner_btn = button("AI 초안 만들기", "primary", "sparkles", on_click=self.generate)
        self.banner.setLayout(
            hbox(IconBadge("sparkles", "accent", 40), vbox(make_label("아직 업무일지가 없어요", "section"), self.banner_text, spacing=2), None, banner_btn, spacing=14, margins=(18, 16, 18, 16))
        )

        self.summary = QLineEdit()
        self.summary.setProperty("flat", True)
        self.summary.setProperty("level", "heading")
        self.summary.setPlaceholderText("오늘 업무를 한 줄로 요약해 보세요")
        self.summary.textEdited.connect(self._changed)
        summary_card = Card("한 줄 요약")
        summary_card.body.addWidget(self.summary)

        self.accomplishments = ItemList(show_time=True, add_label="실적 추가")
        self.plans = ItemList(show_time=False, add_label="계획 추가")
        self.issues = ItemList(show_time=False, add_label="이슈 추가", empty_text="특이 사항이 없으면 비워 두세요.")
        self.acc_card = Card("금일 실적")
        self.acc_card.body.addWidget(self.accomplishments)
        self.plan_card = Card("명일 계획")
        self.plan_card.body.addWidget(self.plans)
        self.issue_card = Card("이슈 및 협조 요청")
        self.issue_card.body.addWidget(self.issues)
        for lst in (self.accomplishments, self.plans, self.issues):
            lst.changed.connect(self._changed)

        self.left = QWidget()
        self.left.setLayout(vbox(self.banner, summary_card, self.acc_card, self.plan_card, self.issue_card, None, spacing=14))

        # 오른쪽: 메모·내 할 일·비고
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("메모 입력 후 Enter")
        self.note_input.returnPressed.connect(self.add_note)
        self.notes_box = QVBoxLayout()
        self.notes_box.setSpacing(2)
        notes_card = Card("메모", "초안에 반영돼요")
        notes_card.body.addWidget(self.note_input)
        notes_card.body.addLayout(self.notes_box)

        self.todo_box = QVBoxLayout()
        self.todo_box.setSpacing(4)
        self.todo_card = Card("내 할 일", "회의 액션아이템")
        self.todo_card.body.addLayout(self.todo_box)

        self.memo = AutoTextEdit("보고서 하단에 들어갈 비고", min_lines=3, flat=False)
        self.memo.textChanged.connect(self._changed)
        memo_card = Card("비고")
        memo_card.body.addWidget(self.memo)

        right = QWidget()
        right.setFixedWidth(340)
        right.setLayout(vbox(notes_card, self.todo_card, memo_card, None, spacing=14))

        body = QWidget()
        cols = QHBoxLayout()
        cols.setSpacing(16)
        cols.addWidget(self.left, 1)
        cols.addWidget(right, 0, Qt.AlignTop)
        body.setLayout(cols)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 0)
        layout.setSpacing(16)
        layout.addLayout(head)
        scroll = scroll_area(body)
        scroll.widget().layout().setContentsMargins(0, 0, 0, 28)
        layout.addWidget(scroll, 1)
        self.load(self.day)

    # ------------------------------------------------------------ 날짜
    def _shift(self, days: int) -> None:
        self.open_date((date.fromisoformat(self.day) + timedelta(days=days)).isoformat())

    def open_date(self, day: str) -> None:
        if day == self.day:
            return
        self.flush()
        self.load(day)

    # ------------------------------------------------------------ 로드·저장
    def load(self, day: str) -> None:
        self._loading = True
        self.day = day
        self.date_edit.blockSignals(True)
        self.date_edit.setDate(QDate.fromString(day, "yyyy-MM-dd"))
        self.date_edit.blockSignals(False)
        report = self.services.db.get_report(day)
        self._last_saved = report.updated_at if report else None
        report = report or DailyReport(date=day)
        self.summary.setText(report.summary)
        self.accomplishments.set_items(report.accomplishments)
        self.plans.set_items(report.plans)
        self.issues.set_items(report.issues)
        self.memo.setPlainText(report.memo)
        self._loading = False
        self.reload_side()
        self._update_state(report)

    def reload_side(self) -> None:
        self._load_notes()
        self._load_todos()

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self.saved_label.setText("저장 중…")
        self.save_timer.start()
        self._update_totals()

    def _autosave(self) -> None:
        report = self.current_report()
        self.services.db.save_report(report)
        self._last_saved = report.updated_at
        self._update_state(report)

    def flush(self) -> None:
        """대기 중인 자동 저장을 즉시 처리한다."""
        if self.save_timer.isActive():
            self.save_timer.stop()
            self._autosave()

    def is_dirty(self) -> bool:
        return self.save_timer.isActive()

    def confirm_discard(self) -> bool:
        self.flush()
        return True

    def current_report(self) -> DailyReport:
        report = self.services.db.get_report(self.day) or DailyReport(date=self.day)
        report.summary = self.summary.text().strip()
        report.accomplishments = self.accomplishments.items()
        report.plans = self.plans.items()
        report.issues = self.issues.items()
        report.memo = self.memo.toPlainText().strip()
        return report

    def save(self, final: bool | None = None) -> None:
        self.save_timer.stop()
        report = self.current_report()
        if final is not None:
            report.status = "final" if final else "draft"
        self.services.db.save_report(report)
        self._last_saved = report.updated_at
        self._update_state(report)

    def toggle_final(self) -> None:
        report = self.services.db.get_report(self.day)
        final = not (report and report.status == "final")
        self.save(final=final)
        self.status_message.emit(f"{self.day} 업무일지를 {'확정했어요' if final else '초안으로 되돌렸어요'}.", "success")

    def _update_totals(self) -> None:
        acc = self.accomplishments.items()
        minutes = sum(i.time_spent_min for i in acc)
        self.acc_card.set_caption(f"{len(acc)}건" + (f" · {format_duration(minutes * 60)}" if minutes else ""))
        self.plan_card.set_caption(f"{len(self.plans.items())}건" if self.plans.items() else "")
        self.issue_card.set_caption(f"{len(self.issues.items())}건" if self.issues.items() else "")

    def _update_state(self, report: DailyReport | None = None) -> None:
        stored = self.services.db.get_report(self.day)
        has_content = bool(stored and (stored.accomplishments or stored.plans or stored.issues or stored.summary))
        if stored is None or not has_content:
            self.state_chip.set_kind("neutral", "작성 전")
        elif stored.status == "final":
            self.state_chip.set_kind("success", "확정됨")
        else:
            self.state_chip.set_kind("warning", "초안")
        final = bool(stored and stored.status == "final")
        self.btn_final.setText("확정 해제" if final else "확정")
        self.saved_label.setText(f"자동 저장됨 · {datetime.fromtimestamp(self._last_saved):%H:%M}" if self._last_saved else "")
        self._update_totals()

        # 비어 있으면 배너로 안내
        empty = not (self.accomplishments.count() or self.plans.count() or self.issues.count() or self.summary.text())
        self.banner.setVisible(empty)
        if empty:
            summary = summarize_day(self.services.db, self.day)
            if summary.active_sec or summary.meetings:
                self.banner_text.setText(
                    f"PC 활동 {format_duration(summary.active_sec)}, 회의 {len(summary.meetings)}건을 바탕으로 금일 실적·명일 계획 초안을 만들어 드려요."
                )
            else:
                self.banner_text.setText("이 날짜에는 기록된 활동이 없어요. 직접 항목을 추가해 작성할 수도 있어요.")

    # ------------------------------------------------------------ AI 초안
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.btn_generate.setEnabled(not busy)
        self.btn_generate.setText("작성 중…" if busy else "AI 초안")
        self.left.setEnabled(not busy)

    def generate(self) -> None:
        if self._busy:
            return
        current = self.current_report()
        if current.accomplishments or current.plans or current.issues:
            answer = QMessageBox.question(self, "AI 초안", "지금 작성된 실적·계획·이슈를 새 초안으로 바꿀까요?\n비고와 메모는 그대로 둡니다.")
            if answer != QMessageBox.Yes:
                return
        self.flush()
        mode = "Claude" if self.services.claude.available() else "사용 시간 통계"
        self._set_busy(True)
        self.status_message.emit(f"{mode}로 초안을 작성하고 있어요…", "info")
        day = self.day
        run_async(self.services.generate_report_draft, day, on_done=lambda _r: self._on_generated(day), on_error=self._on_generate_failed)

    def _on_generated(self, day: str) -> None:
        self._set_busy(False)
        if day == self.day:
            self.load(day)
        hint = "" if self.services.claude.available() else " (Claude API 키를 설정하면 더 자연스러운 초안을 받을 수 있어요)"
        self.status_message.emit(f"초안을 만들었어요. 확인하고 다듬어 주세요.{hint}", "success")

    def _on_generate_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_message.emit(f"초안을 만들지 못했어요: {message}", "error")

    def reload_if_idle(self, day: str) -> None:
        if day == self.day and not self.is_dirty() and not self._busy:
            self.load(day)

    # ------------------------------------------------------------ 메모
    def _note_ts(self) -> float:
        if self.day == date.today().isoformat():
            return datetime.now().timestamp()
        return datetime.fromisoformat(f"{self.day}T12:00:00").timestamp()

    def add_note(self) -> None:
        text = self.note_input.text().strip()
        if text:
            self.services.db.add_note(text, self._note_ts())
            self.note_input.clear()
            self._load_notes()

    def _load_notes(self) -> None:
        clear_layout(self.notes_box)
        notes = self.services.db.notes_for(self.day)
        if not notes:
            self.notes_box.addWidget(make_label("트레이·오늘 화면에서 남긴 메모도 여기에 모여요.", "caption", wrap=True))
        for note in notes:
            row = QWidget()
            text = make_label(note.text, wrap=True)
            delete = icon_button("x", "메모 삭제", lambda nid=note.id: (self.services.db.delete_note(nid), self._load_notes()), "text3", 14)
            layout = hbox(make_label(datetime.fromtimestamp(note.ts).strftime("%H:%M"), "caption"), text, delete, spacing=8, margins=(0, 2, 0, 2))
            layout.setStretch(1, 1)
            row.setLayout(layout)
            self.notes_box.addWidget(row)

    def _load_todos(self) -> None:
        clear_layout(self.todo_box)
        items = self.services.db.open_my_action_items()
        self.todo_card.setVisible(True)
        if not items:
            self.todo_box.addWidget(make_label("남은 액션아이템이 없어요.", "caption"))
        for item, meeting in items[:10]:
            row = QWidget()
            check = QCheckBox()
            check.setToolTip("완료로 표시")
            check.toggled.connect(lambda on, iid=item.id: self._todo_done(iid, on))
            task = ElidedLabel(item.task)
            caption = make_label(f"{meeting.title}" + (f" · ~{item.due}" if item.due else ""), "caption")
            add = icon_button("plus", "명일 계획에 추가", lambda it=item, mt=meeting: self._todo_to_plan(it.task, mt.title), "text2", 16)
            info = vbox(task, caption, spacing=0)
            layout = hbox(check, info, add, spacing=8, margins=(0, 3, 0, 3))
            layout.setStretch(1, 1)
            row.setLayout(layout)
            self.todo_box.addWidget(row)

    def _todo_done(self, item_id: int, done: bool) -> None:
        self.services.db.set_action_item_done(item_id, done)
        self.status_message.emit("액션아이템을 완료로 표시했어요.", "success")
        QTimer.singleShot(300, self._load_todos)

    def _todo_to_plan(self, task: str, meeting_title: str) -> None:
        self.plans.add_item(ReportItem(title=task, detail=f"{meeting_title} 액션아이템", category="액션아이템"))
        self.status_message.emit("명일 계획에 추가했어요.", "success")

    # ------------------------------------------------------------ 내보내기
    def copy_plaintext(self) -> None:
        self.flush()
        QApplication.clipboard().setText(report_to_plaintext(self.current_report()))
        self.status_message.emit("업무일지를 클립보드에 복사했어요. 그룹웨어에 붙여넣으세요.", "success")

    def markdown(self) -> str:
        settings = self.services.store.get()
        return report_to_markdown(
            self.current_report(),
            summarize_day(self.services.db, self.day),
            author=settings.user_name,
            action_items=self.services.db.open_my_action_items(),
        )

    def export_markdown(self) -> None:
        self.flush()
        default = str(paths.documents_dir() / f"업무일지_{self.day}.md")
        path, _ = QFileDialog.getSaveFileName(self, "Markdown 내보내기", default, "Markdown (*.md)")
        if path:
            Path(path).write_text(self.markdown(), encoding="utf-8")
            self.status_message.emit(f"저장했어요: {path}", "success")
