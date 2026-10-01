"""오프스크린 UI 스모크 테스트: 가짜 수집기·STT·Claude 로 주요 흐름을 끝까지 돌린다."""

import os
import time
from datetime import date, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from audio_util import tone, write_wav  # noqa: E402
from fakes import FakeClient, parsed  # noqa: E402
from test_meeting import FakeBackend, minutes  # noqa: E402
from workreport.collector.base import WindowInfo  # noqa: E402
from workreport.collector.fake import FakeProbe  # noqa: E402
from workreport.meeting.stt.fake import FakeTranscriber  # noqa: E402
from workreport.models import ActivitySession, DailyReportDraft, MeetingMinutes, MeetingStatus, ReportItem  # noqa: E402
from workreport.services import Services, SettingsStore  # noqa: E402

TODAY = date.today().isoformat()


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))


def wait_until(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError("시간 초과")


class RoutingClient(FakeClient):
    """요청 스키마에 따라 업무일지 초안 또는 회의록을 돌려준다."""

    def __init__(self):
        super().__init__()
        self.default = self.route

    def route(self, kwargs):
        if kwargs.get("output_format") is MeetingMinutes:
            return parsed(minutes())
        return parsed(DailyReportDraft(summary="AI 요약", accomplishments=[ReportItem(title="로그인 오류 수정", detail="토큰 만료 처리", time_spent_min=90)], plans=[ReportItem(title="배포 준비")], issues=[]))


@pytest.fixture
def services(db, tmp_path):
    store = SettingsStore(tmp_path / "config.json")
    s = store.get()
    s.onboarding_done = True
    s.user_name = "홍길동"
    svc = Services(
        store,
        db,
        probe=FakeProbe(WindowInfo("Visual Studio Code", "auth.py - backend", "Code.exe")),
        transcriber_factory=lambda _s: FakeTranscriber(),
        claude_client_factory=lambda: RoutingClient(),
        claude_configured=lambda: True,
        recorder_backend=FakeBackend(),
    )
    nine = datetime.combine(date.today(), datetime.min.time()).timestamp() + 9 * 3600  # 오늘 09:00 (자정 무관)
    db.insert_session(ActivitySession(nine, nine + 3000, "Visual Studio Code", "auth.py - backend - Visual Studio Code"))
    return svc


def test_main_window_report_flow(qapp, services):
    from workreport.ui.main_window import MainWindow

    win = MainWindow(services)
    win.show()
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        QApplication.processEvents()
    assert win.today.apps.rowCount() >= 1

    editor = win.report
    win.tabs.setCurrentIndex(win.TAB_REPORT)
    editor.generate()
    wait_until(lambda: editor.accomplishments.table.rowCount() == 1)
    assert editor.summary.text() == "AI 요약"
    assert editor.accomplishments.items()[0].time_spent_min == 90

    editor.memo.setPlainText("QA 일정 확인")
    editor.save(final=True)
    stored = services.db.get_report(TODAY)
    assert stored.status == "final" and stored.memo == "QA 일정 확인"

    editor.copy_plaintext()
    assert "로그인 오류 수정 (1시간 30분)" in QApplication.clipboard().text()
    assert "## 부록 A. 앱별 사용 시간" in editor.markdown()

    win.history.refresh()
    assert "로그인 오류 수정" in win.history.preview.toPlainText()
    win.quitting = True
    win.close()


def test_item_editor_roundtrip(qapp):
    from workreport.ui.common import ItemTableEditor

    ed = ItemTableEditor()
    ed.set_items([ReportItem(title="A", detail="x\ny", time_spent_min=10), ReportItem(title="B")])
    ed.table.setCurrentCell(1, 0)
    ed.move(-1)
    assert [i.title for i in ed.items()] == ["B", "A"]
    assert ed.items()[1].detail == "x\ny"
    ed.table.selectRow(0)
    ed.remove_row()
    assert [i.title for i in ed.items()] == ["A"]


def test_meetings_view_flow(qapp, services, tmp_path):
    from workreport.ui.meeting_view import MeetingsView, discussion_to_text, text_to_discussion

    view = MeetingsView(services)
    services.pipeline.listeners.append(lambda mid: None)
    meeting = services.import_audio(write_wav(tmp_path / "주간회의.wav", tone(1)))
    services.pipeline.process(meeting.id)
    view.refresh_list()
    view.select_meeting(meeting.id)
    QApplication.processEvents()
    assert view.current_id == meeting.id
    assert view.summary.toPlainText() == "금요일 배포 확정"
    assert view.actions.rowCount() == 3
    assert view.transcript.count() == 2

    view.decisions.setPlainText("배포는 금요일\n롤백 계획 수립")
    view.actions.item(0, 0).setCheckState(Qt.Checked)
    view.save_minutes()
    stored = services.db.get_meeting(meeting.id)
    assert stored.minutes.decisions == ["배포는 금요일", "롤백 계획 수립"]
    assert services.db.action_items_for(meeting.id)[0].done
    assert "롤백 계획 수립" in view.current_markdown()

    topics = text_to_discussion("## 일정\n- 금요일 배포\n- QA 목요일\n## 인력\n- 1명 충원")
    assert [t.topic for t in topics] == ["일정", "인력"] and topics[0].points == ["금요일 배포", "QA 목요일"]
    assert text_to_discussion(discussion_to_text(topics)) == topics

    # 앱 내장 녹음 → 회의 등록
    view.toggle_recording()
    assert services.recorder.is_recording
    time.sleep(0.3)
    view.toggle_recording()
    assert not services.recorder.is_recording
    assert len(services.db.list_meetings()) == 2

    view.delete_meeting()
    assert len(services.db.list_meetings()) == 1


def test_settings_view_save(qapp, services):
    from workreport.ui.settings_view import SettingsView

    view = SettingsView(services)
    view.user_name.setText("김영희")
    view.categories.setPlainText("title | PROJ-\\d+ | PROJ\nbad line\napp | Excel | 문서")
    view.stt_engine.setCurrentIndex(view.stt_engine.findData("azure"))
    view.claude_key.setText("sk-ant-test")
    view.save()
    s = services.store.get()
    assert s.user_name == "김영희" and s.stt_engine == "azure"
    assert [(r.field, r.category) for r in s.category_rules] == [("title", "PROJ"), ("app", "문서")]
    from workreport import credentials

    assert credentials.get_secret(credentials.ANTHROPIC_API_KEY) == "sk-ant-test"
    assert view.claude_key.text() == ""


def test_application_wiring(qapp, services):
    from workreport.app import Application

    app = Application(qapp, services, start_minimized=True)
    notes = []
    app.tray.notify = lambda title, message, on_click=None: notes.append(title)
    app.auto_draft(TODAY)
    wait_until(lambda: "업무일지 초안이 준비되었습니다" in notes)
    assert services.db.get_report(TODAY).summary == "AI 요약"

    app._on_meeting_detected("주간회의")
    assert notes[-1] == "회의 중인가요?"

    services.pipeline.start()
    meeting = services.import_audio(_wav(services))
    wait_until(lambda: services.db.get_meeting(meeting.id).status == MeetingStatus.DONE)
    wait_until(lambda: "회의록 준비 완료" in notes)
    app.window.quitting = True
    app.timer.stop()
    app.tray.hide()
    services.stop()


def _wav(services):
    from workreport import paths

    return write_wav(paths.audio_dir() / "import.wav", tone(1))
