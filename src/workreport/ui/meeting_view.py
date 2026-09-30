"""회의록 탭: 녹음 시작·중지, 파일 가져오기, 회의 목록·진행률, 회의록 편집, 전사문·재생."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..models import ActionItem, DiscussionTopic, Meeting, MeetingMinutes, MeetingStatus, format_hms
from ..report.render import format_duration, hm, minutes_to_markdown
from ..services import Services
from .common import RECORDING, ro_item, run_async

AUDIO_FILTER = "오디오 (*.m4a *.mp3 *.wav *.flac *.wma *.aac *.ogg)"


def discussion_to_text(topics: list[DiscussionTopic]) -> str:
    lines = []
    for topic in topics:
        lines.append(f"## {topic.topic}")
        lines += [f"- {p}" for p in topic.points]
    return "\n".join(lines)


def text_to_discussion(text: str) -> list[DiscussionTopic]:
    topics: list[DiscussionTopic] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            topics.append(DiscussionTopic(topic=line.lstrip("# ").strip(), points=[]))
        else:
            if not topics:
                topics.append(DiscussionTopic(topic="논의", points=[]))
            topics[-1].points.append(line.lstrip("-•* ").strip())
    return topics


def _lines(text: str) -> list[str]:
    return [line.lstrip("-•* ").strip() for line in text.splitlines() if line.strip()]


class MeetingsView(QWidget):
    status_message = Signal(str)
    recording_changed = Signal(bool)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.current_id: int | None = None
        self._dirty = False
        self._loading = False
        self._player = None

        # 도구 모음
        self.btn_record = QPushButton()
        self.btn_record.clicked.connect(self.toggle_recording)
        btn_import = QPushButton("파일 가져오기")
        btn_import.clicked.connect(self.import_file)
        self.btn_resummarize = QPushButton("회의록 다시 작성")
        self.btn_resummarize.clicked.connect(lambda: self.reprocess(force_transcribe=False))
        self.btn_retranscribe = QPushButton("음성 다시 변환")
        self.btn_retranscribe.clicked.connect(lambda: self.reprocess(force_transcribe=True))
        self.btn_delete = QPushButton("삭제")
        self.btn_delete.clicked.connect(self.delete_meeting)
        self.btn_copy = QPushButton("Markdown 복사")
        self.btn_copy.clicked.connect(self.copy_markdown)
        self.btn_export = QPushButton("Markdown 내보내기")
        self.btn_export.clicked.connect(self.export_markdown)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.btn_record)
        toolbar.addWidget(btn_import)
        toolbar.addStretch()
        for btn in (self.btn_resummarize, self.btn_retranscribe, self.btn_copy, self.btn_export, self.btn_delete):
            toolbar.addWidget(btn)

        # 목록
        self.list = QTableWidget(0, 5)
        self.list.setHorizontalHeaderLabels(["날짜", "시간", "제목", "길이", "상태"])
        self.list.verticalHeader().setVisible(False)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.list.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.list.itemSelectionChanged.connect(self._on_selection)

        # 상세: 회의록
        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        self.title = QLineEdit()
        self.summary = QPlainTextEdit()
        self.attendees = QLineEdit()
        self.attendees.setPlaceholderText("쉼표로 구분")
        self.discussion = QPlainTextEdit()
        self.discussion.setPlaceholderText("## 주제\n- 논의 내용")
        self.decisions = QPlainTextEdit()
        self.decisions.setPlaceholderText("한 줄에 하나씩")
        self.open_questions = QPlainTextEdit()
        self.open_questions.setPlaceholderText("한 줄에 하나씩")
        self.actions = QTableWidget(0, 5)
        self.actions.setHorizontalHeaderLabels(["완료", "담당", "할 일", "기한", "내 일"])
        self.actions.verticalHeader().setVisible(False)
        ah = self.actions.horizontalHeader()
        ah.setSectionResizeMode(QHeaderView.ResizeToContents)
        ah.setSectionResizeMode(2, QHeaderView.Stretch)
        btn_add_action = QPushButton("액션아이템 추가")
        btn_add_action.clicked.connect(lambda: self._append_action(ActionItem(meeting_id=self.current_id or 0, task="")))
        btn_del_action = QPushButton("선택 삭제")
        btn_del_action.clicked.connect(self._remove_action)
        self.btn_save = QPushButton("회의록 저장")
        self.btn_save.clicked.connect(self.save_minutes)

        for w in (self.title, self.attendees):
            w.textEdited.connect(self._mark_dirty)
        for w in (self.summary, self.discussion, self.decisions, self.open_questions):
            w.textChanged.connect(self._mark_dirty)
        self.actions.itemChanged.connect(self._mark_dirty)

        form = QFormLayout()
        form.addRow("제목", self.title)
        form.addRow("참석자", self.attendees)
        form.addRow("요약", self.summary)
        form.addRow("논의 내용", self.discussion)
        form.addRow("결정 사항", self.decisions)
        form.addRow("미결 사항", self.open_questions)
        action_box = QVBoxLayout()
        action_box.addWidget(self.actions)
        row = QHBoxLayout()
        row.addWidget(btn_add_action)
        row.addWidget(btn_del_action)
        row.addStretch()
        row.addWidget(self.btn_save)
        action_box.addLayout(row)
        form.addRow("액션 아이템", action_box)
        minutes_page = QWidget()
        page_layout = QVBoxLayout(minutes_page)
        page_layout.addWidget(self.lbl_info)
        page_layout.addLayout(form)

        # 상세: 전사문
        self.transcript = QListWidget()
        self.transcript.setWordWrap(True)
        self.transcript.itemDoubleClicked.connect(self._play_from)
        self.btn_play = QPushButton("▶ 재생")
        self.btn_play.clicked.connect(self._toggle_play)
        self.lbl_player = QLabel("항목을 더블클릭하면 해당 위치부터 재생합니다.")
        transcript_page = QWidget()
        tl = QVBoxLayout(transcript_page)
        tl.addWidget(self.transcript)
        pr = QHBoxLayout()
        pr.addWidget(self.btn_play)
        pr.addWidget(self.lbl_player, 1)
        tl.addLayout(pr)

        self.detail = QTabWidget()
        self.detail.addTab(minutes_page, "회의록")
        self.detail.addTab(transcript_page, "전사문")

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.list)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(splitter, 1)

        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(1000)
        self.rec_timer.timeout.connect(self._update_record_button)
        self.rec_timer.start()
        self._update_record_button()
        self.refresh_list()
        self._show(None)

    # ------------------------------------------------------------ 녹음
    def _update_record_button(self) -> None:
        rec = self.services.recorder
        if rec.needs_finalize:  # 장치 오류로 녹음이 멈춤 → 지금까지 녹음된 부분을 회의로 등록
            self.toggle_recording()
            return
        if rec.is_recording:
            self.btn_record.setText(f"■ 녹음 중지 ({format_hms(rec.elapsed())})")
            self.btn_record.setStyleSheet(f"color: white; background: {RECORDING}; font-weight: 600; padding: 4px 10px;")
        else:
            self.btn_record.setText("● 회의 녹음 시작")
            self.btn_record.setStyleSheet("font-weight: 600; padding: 4px 10px;")

    def toggle_recording(self, title_hint: str = "") -> None:
        rec = self.services.recorder
        try:
            if rec.is_recording or rec.needs_finalize:
                meeting = self.services.stop_recording()
                self.status_message.emit(f"녹음을 마쳤습니다. '{meeting.title}' 음성 변환을 시작합니다.")
                self.recording_changed.emit(False)
                self.select_meeting(meeting.id)
            else:
                self.services.start_recording(title_hint if isinstance(title_hint, str) else "")
                self.status_message.emit("회의 녹음을 시작했습니다. (마이크 + PC 소리)")
                self.recording_changed.emit(True)
        except Exception as exc:
            QMessageBox.warning(self, "녹음 오류", f"녹음을 처리하지 못했습니다.\n{exc}")
        self._update_record_button()

    # ------------------------------------------------------------ 목록
    def refresh_list(self) -> None:
        meetings = self.services.db.list_meetings()
        self.list.blockSignals(True)
        self.list.setRowCount(len(meetings))
        selected_row = -1
        for row, m in enumerate(meetings):
            self.list.setItem(row, 0, ro_item(m.date))
            self.list.setItem(row, 1, ro_item(f"{hm(m.started_at)}~{hm(m.ended_at)}"))
            title = ro_item(m.title)
            title.setData(Qt.UserRole, m.id)
            self.list.setItem(row, 2, title)
            self.list.setItem(row, 3, ro_item(format_duration(m.duration_sec)))
            self.list.setItem(row, 4, ro_item(self._status_text(m)))
            if m.id == self.current_id:
                selected_row = row
        if selected_row >= 0:
            self.list.selectRow(selected_row)
        self.list.blockSignals(False)

    @staticmethod
    def _status_text(m: Meeting) -> str:
        label = MeetingStatus.LABELS.get(m.status, m.status)
        if m.status == MeetingStatus.TRANSCRIBING:
            label += f" {int(m.progress * 100)}%"
        return label

    def on_meeting_changed(self, meeting_id: int) -> None:
        meeting = self.services.db.get_meeting(meeting_id)
        row = self._row_of(meeting_id)
        if meeting is None or row < 0:
            self.refresh_list()
        else:
            self.list.item(row, 2).setText(meeting.title)
            self.list.item(row, 4).setText(self._status_text(meeting))
            self.list.item(row, 3).setText(format_duration(meeting.duration_sec))
        if meeting_id == self.current_id and meeting is not None:
            if meeting.status in (MeetingStatus.DONE, MeetingStatus.ERROR, MeetingStatus.TRANSCRIBED) and not self._dirty:
                self._show(meeting)
            else:
                self._update_info(meeting)

    def _row_of(self, meeting_id: int) -> int:
        for row in range(self.list.rowCount()):
            if self.list.item(row, 2).data(Qt.UserRole) == meeting_id:
                return row
        return -1

    def select_meeting(self, meeting_id: int) -> None:
        if self._row_of(meeting_id) < 0:
            self.refresh_list()
        row = self._row_of(meeting_id)
        if row >= 0:
            self.list.selectRow(row)

    def _on_selection(self) -> None:
        rows = self.list.selectionModel().selectedRows()
        new_id = self.list.item(rows[0].row(), 2).data(Qt.UserRole) if rows else None
        if new_id == self.current_id:
            return
        if self._dirty:
            answer = QMessageBox.question(self, "저장하지 않은 변경", "회의록 변경 사항을 저장할까요?", QMessageBox.Save | QMessageBox.Discard)
            if answer == QMessageBox.Save:
                self.save_minutes()
        self.current_id = new_id
        self._show(self.services.db.get_meeting(new_id) if new_id else None)

    # ------------------------------------------------------------ 상세
    def _update_info(self, m: Meeting) -> None:
        src = {"recorder_app": "녹음기 앱", "in_app": "앱 녹음", "import": "가져온 파일"}.get(m.source, m.source)
        parts = [
            f"{m.date} {hm(m.started_at)}~{hm(m.ended_at)} ({format_duration(m.duration_sec)})",
            f"출처: {src}",
            f"상태: {self._status_text(m)}",
        ]
        if m.stt_engine:
            parts.append(f"STT: {m.stt_engine}")
        if m.error:
            parts.append(f"⚠ {m.error}")
        self.lbl_info.setText(" · ".join(parts))

    def _show(self, m: Meeting | None) -> None:
        self._loading = True
        enabled = m is not None
        for w in (self.btn_resummarize, self.btn_retranscribe, self.btn_delete, self.btn_copy, self.btn_export, self.btn_save, self.detail):
            w.setEnabled(enabled)
        self.transcript.clear()
        self.actions.setRowCount(0)
        if m is None:
            self.lbl_info.setText("회의를 선택하세요. 녹음기 앱으로 녹음한 파일은 자동으로 이 목록에 추가됩니다.")
            for w in (self.title, self.attendees):
                w.clear()
            for w in (self.summary, self.discussion, self.decisions, self.open_questions):
                w.clear()
        else:
            self._update_info(m)
            minutes = m.minutes
            self.title.setText(m.title)
            self.attendees.setText(", ".join(minutes.attendees) if minutes else "")
            self.summary.setPlainText(minutes.summary if minutes else "")
            self.discussion.setPlainText(discussion_to_text(minutes.discussion) if minutes else "")
            self.decisions.setPlainText("\n".join(minutes.decisions) if minutes else "")
            self.open_questions.setPlainText("\n".join(minutes.open_questions) if minutes else "")
            for item in self.services.db.action_items_for(m.id):
                self._append_action(item)
            if m.transcript:
                for seg in m.transcript.segments:
                    speaker = f"{seg.speaker}: " if seg.speaker else ""
                    li = QListWidgetItem(f"[{format_hms(seg.start)}] {speaker}{seg.text}")
                    li.setData(Qt.UserRole, seg.start)
                    self.transcript.addItem(li)
            self.btn_play.setEnabled(bool(m.audio_path) and Path(m.audio_path).exists())
        self._loading = False
        self._dirty = False

    def _append_action(self, item: ActionItem) -> None:
        self.actions.blockSignals(True)
        row = self.actions.rowCount()
        self.actions.insertRow(row)
        done = QTableWidgetItem()
        done.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        done.setCheckState(Qt.Checked if item.done else Qt.Unchecked)
        done.setData(Qt.UserRole, item.id)
        self.actions.setItem(row, 0, done)
        self.actions.setItem(row, 1, QTableWidgetItem(item.owner))
        self.actions.setItem(row, 2, QTableWidgetItem(item.task))
        self.actions.setItem(row, 3, QTableWidgetItem(item.due))
        mine = QTableWidgetItem()
        mine.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        mine.setCheckState(Qt.Checked if item.is_mine else Qt.Unchecked)
        self.actions.setItem(row, 4, mine)
        self.actions.blockSignals(False)
        if not self._loading:
            self._mark_dirty()

    def _remove_action(self) -> None:
        for row in sorted({i.row() for i in self.actions.selectedIndexes()}, reverse=True):
            self.actions.removeRow(row)
            self._mark_dirty()

    def _mark_dirty(self, *_args) -> None:
        if not self._loading and self.current_id is not None:
            self._dirty = True

    def _collect_actions(self) -> list[ActionItem]:
        items = []
        for row in range(self.actions.rowCount()):
            text = lambda c: self.actions.item(row, c).text().strip() if self.actions.item(row, c) else ""
            task = text(2)
            if not task:
                continue
            items.append(
                ActionItem(
                    meeting_id=self.current_id or 0,
                    owner=text(1),
                    task=task,
                    due=text(3),
                    done=self.actions.item(row, 0).checkState() == Qt.Checked,
                    is_mine=self.actions.item(row, 4).checkState() == Qt.Checked,
                )
            )
        return items

    def save_minutes(self) -> None:
        if self.current_id is None:
            return
        meeting = self.services.db.get_meeting(self.current_id)
        if meeting is None:
            return
        actions = self._collect_actions()
        minutes = MeetingMinutes(
            title=self.title.text().strip(),
            summary=self.summary.toPlainText().strip(),
            attendees=[a.strip() for a in self.attendees.text().split(",") if a.strip()],
            discussion=text_to_discussion(self.discussion.toPlainText()),
            decisions=_lines(self.decisions.toPlainText()),
            action_items=[{"owner": a.owner, "task": a.task, "due": a.due, "is_mine": a.is_mine} for a in actions],
            open_questions=_lines(self.open_questions.toPlainText()),
        )
        fields = {"minutes": minutes}
        if minutes.title:
            fields["title"] = minutes.title
        if meeting.status in (MeetingStatus.TRANSCRIBED, MeetingStatus.ERROR) and meeting.transcript:
            fields["status"] = MeetingStatus.DONE
            fields["error"] = ""
        self.services.db.update_meeting(self.current_id, **fields)
        self.services.db.replace_action_items(self.current_id, actions)
        self._dirty = False
        self.on_meeting_changed(self.current_id)
        self.status_message.emit("회의록을 저장했습니다.")

    # ------------------------------------------------------------ 작업
    def import_file(self) -> None:
        start_dir = str(self.services.store.get().recorder_path())
        path, _ = QFileDialog.getOpenFileName(self, "회의 녹음 파일 가져오기", start_dir, AUDIO_FILTER)
        if not path:
            return
        run_async(
            self.services.import_audio,
            Path(path),
            on_done=lambda m: (self.refresh_list(), self.select_meeting(m.id), self.status_message.emit(f"'{m.title}' 음성 변환을 시작합니다.")),
            on_error=lambda e: QMessageBox.warning(self, "가져오기 실패", e),
        )

    def reprocess(self, force_transcribe: bool) -> None:
        if self.current_id is None:
            return
        meeting = self.services.db.get_meeting(self.current_id)
        if force_transcribe and not (meeting and meeting.audio_path and Path(meeting.audio_path).exists()):
            QMessageBox.information(self, "음성 다시 변환", "오디오 파일이 없어 다시 변환할 수 없습니다.")
            return
        if not force_transcribe and not (meeting and meeting.transcript):
            force_transcribe = True
        if not force_transcribe and not self.services.claude.available():
            QMessageBox.information(self, "회의록 다시 작성", "설정 탭에서 Claude API 키를 입력해야 회의록을 작성할 수 있습니다.")
            return
        if meeting.minutes and QMessageBox.question(self, "다시 처리", "현재 회의록을 새로 작성한 내용으로 바꿉니다. 계속할까요?") != QMessageBox.Yes:
            return
        self.services.db.update_meeting(self.current_id, status=MeetingStatus.QUEUED, progress=0.0, error="")
        self.services.pipeline.enqueue(self.current_id, force_transcribe=force_transcribe)
        self.on_meeting_changed(self.current_id)

    def delete_meeting(self) -> None:
        if self.current_id is None:
            return
        meeting = self.services.db.get_meeting(self.current_id)
        if QMessageBox.question(self, "회의 삭제", f"'{meeting.title}' 회의와 회의록을 삭제할까요?\n(녹음기 앱 폴더의 원본 파일은 지우지 않습니다)") != QMessageBox.Yes:
            return
        own = paths.audio_dir().resolve()
        audio = Path(meeting.audio_path) if meeting.audio_path else None
        if audio and audio.exists() and own in audio.resolve().parents:
            audio.unlink(missing_ok=True)
        self.services.db.delete_meeting(self.current_id)
        self.current_id = None
        self._dirty = False
        self.refresh_list()
        self._show(None)

    def current_markdown(self) -> str:
        meeting = self.services.db.get_meeting(self.current_id)
        return minutes_to_markdown(meeting, self.services.db.action_items_for(meeting.id))

    def copy_markdown(self) -> None:
        if self.current_id is not None:
            QApplication.clipboard().setText(self.current_markdown())
            self.status_message.emit("회의록을 클립보드에 복사했습니다.")

    def export_markdown(self) -> None:
        if self.current_id is None:
            return
        meeting = self.services.db.get_meeting(self.current_id)
        safe = "".join(ch for ch in meeting.title if ch not in '\\/:*?"<>|').strip() or "회의록"
        default = str(paths.documents_dir() / f"회의록_{meeting.date}_{safe}.md")
        path, _ = QFileDialog.getSaveFileName(self, "회의록 내보내기", default, "Markdown (*.md)")
        if path:
            Path(path).write_text(self.current_markdown(), encoding="utf-8")
            self.status_message.emit(f"저장했습니다: {path}")

    # ------------------------------------------------------------ 재생
    def _ensure_player(self):
        if self._player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._audio_out = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio_out)
            self._player.playbackStateChanged.connect(
                lambda state: self.btn_play.setText("⏸ 일시정지" if state == QMediaPlayer.PlayingState else "▶ 재생")
            )
            self._player.positionChanged.connect(lambda pos: self.lbl_player.setText(f"재생 위치 {format_hms(pos / 1000)}"))
        return self._player

    def _load_audio(self) -> bool:
        meeting = self.services.db.get_meeting(self.current_id) if self.current_id else None
        if not meeting or not meeting.audio_path or not Path(meeting.audio_path).exists():
            self.lbl_player.setText("오디오 파일이 없습니다.")
            return False
        player = self._ensure_player()
        url = QUrl.fromLocalFile(meeting.audio_path)
        if player.source() != url:
            player.setSource(url)
        return True

    def _play_from(self, item: QListWidgetItem) -> None:
        if self._load_audio():
            self._player.setPosition(int(item.data(Qt.UserRole) * 1000))
            self._player.play()

    def _toggle_play(self) -> None:
        if not self._load_audio():
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()
