"""오프스크린 UI 스모크 테스트: 가짜 수집기·STT·Claude 로 주요 흐름을 끝까지 돌린다."""

import os
import time
from datetime import date, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from audio_util import tone, write_wav  # noqa: E402
from fakes import FakeClient, parsed  # noqa: E402
from test_meeting import FakeBackend, minutes  # noqa: E402
from workreport.collector.base import WindowInfo  # noqa: E402
from workreport.collector.fake import FakeProbe  # noqa: E402
from workreport.meeting.stt.fake import FakeTranscriber  # noqa: E402
from workreport.models import ActivitySession, DailyReportDraft, MeetingMinutes, MeetingStatus, ReportItem  # noqa: E402
from workreport.services import Services, SettingsStore  # noqa: E402
from workreport.ui import theme  # noqa: E402

TODAY = date.today().isoformat()


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.setup_application(app, "light")
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


def pump(n=5):
    for _ in range(n):
        QApplication.processEvents()
        time.sleep(0.01)


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
    db.insert_session(ActivitySession(nine + 3000, nine + 3600, "Microsoft Edge", "PROJ-12 - Jira - Microsoft Edge"))
    return svc


def test_main_window_navigation_and_dashboard(qapp, services):
    from workreport.ui.main_window import MainWindow

    win = MainWindow(services)
    win.show()
    for key in win.PAGES:
        win.go(key)
        pump()
        assert win.current_page() == key and win.sidebar.nav[key].isChecked()
    win.go("today")
    assert win.today.usage.row_count() == 2
    assert len(win.today.timeline.segments) == 2
    assert win.today.stat_active.value.text() == "1시간"
    assert win.today.header.title.text().endswith("홍길동님")

    win.today.note_input.setText("QA 일정 확인")
    win.today._add_note()
    assert [n.text for n in services.db.notes_for(TODAY)] == ["QA 일정 확인"]

    win.show_status("저장했어요", "success")
    assert win.toast.message == "저장했어요"
    win.quitting = True
    win.close()


def test_report_editor_generate_autosave_and_export(qapp, services):
    from workreport.ui.report_editor import ReportEditor

    editor = ReportEditor(services)
    editor.show()
    assert editor.banner.isVisible()
    editor.generate()
    wait_until(lambda: editor.accomplishments.count() == 1)
    assert editor.summary.text() == "AI 요약"
    assert editor.accomplishments.items()[0].time_spent_min == 90
    assert editor.state_chip.text() == "초안"
    assert not editor.banner.isVisible()

    editor.memo.setPlainText("QA 일정 확인")
    assert editor.is_dirty()
    wait_until(lambda: not editor.is_dirty())  # 자동 저장
    assert services.db.get_report(TODAY).memo == "QA 일정 확인"

    editor.toggle_final()
    assert services.db.get_report(TODAY).status == "final"
    assert editor.state_chip.text() == "확정됨" and editor.btn_final.text() == "확정 해제"

    editor.copy_plaintext()
    assert "로그인 오류 수정 (1시간 30분)" in QApplication.clipboard().text()
    assert "## 부록 A. 앱별 사용 시간" in editor.markdown()

    editor._shift(-1)
    assert editor.day != TODAY and editor.accomplishments.count() == 0
    editor.open_date(TODAY)
    assert editor.accomplishments.count() == 1


def test_item_list_edit_move_remove(qapp, services):
    from workreport.ui.report_editor import ItemList

    lst = ItemList()
    lst.set_items([ReportItem(title="A", detail="x\ny", time_spent_min=10), ReportItem(title="B")])
    lst.move(lst.cards[1], -1)
    assert [i.title for i in lst.items()] == ["B", "A"]
    assert lst.items()[1].detail == "x\ny" and lst.items()[1].time_spent_min == 10
    lst.remove(lst.cards[0])
    assert [i.title for i in lst.items()] == ["A"]
    card = lst.add_item()
    card.title.setText("C")
    card.category.setText("#백엔드")
    assert lst.items()[-1].category == "백엔드"
    assert [c.index_label.text() for c in lst.cards] == ["1", "2"]


def test_report_todo_to_plan(qapp, services, tmp_path):
    from workreport.ui.report_editor import ReportEditor

    meeting = services.import_audio(write_wav(tmp_path / "주간회의.wav", tone(1)))
    services.pipeline.process(meeting.id)
    editor = ReportEditor(services)
    editor.reload_side()
    assert editor.todo_box.count() == 2  # 홍길동(본인) + '나' 담당
    item, mt = services.db.open_my_action_items()[0]
    editor._todo_to_plan(item.task, mt.title)
    assert editor.plans.items()[-1].title == item.task


def test_meetings_view_flow(qapp, services, tmp_path):
    from workreport.ui.meeting_view import MeetingsView, discussion_to_text, text_to_discussion

    view = MeetingsView(services)
    view.show()
    assert view.detail_stack.currentWidget() is view.empty
    meeting = services.import_audio(write_wav(tmp_path / "주간회의.wav", tone(1)))
    services.pipeline.process(meeting.id)
    view.refresh_list()
    view.select_meeting(meeting.id)
    pump()
    d = view.detail
    assert view.current_id == meeting.id
    assert d.summary.toPlainText() == "금요일 배포 확정"
    assert len(d.action_rows) == 3
    assert d.transcript.count() == 2
    assert d.status_chip.text() == "완료" and not d.banner.isVisible()

    d.decisions.setPlainText("배포는 금요일\n롤백 계획 수립")
    d.action_rows[0].done.setChecked(True)
    wait_until(lambda: not view.save_timer.isActive())
    stored = services.db.get_meeting(meeting.id)
    assert stored.minutes.decisions == ["배포는 금요일", "롤백 계획 수립"]
    assert services.db.action_items_for(meeting.id)[0].done
    assert "롤백 계획 수립" in view.current_markdown()

    view.search.setText("없는회의")
    assert view.list.item(0).isHidden()
    view.search.setText("주간")
    assert not view.list.item(0).isHidden()

    topics = text_to_discussion("## 일정\n- 금요일 배포\n- QA 목요일\n## 인력\n- 1명 충원")
    assert [t.topic for t in topics] == ["일정", "인력"] and topics[0].points == ["금요일 배포", "QA 목요일"]
    assert text_to_discussion(discussion_to_text(topics)) == topics

    view.delete_meeting()
    assert services.db.list_meetings() == []
    assert view.detail_stack.currentWidget() is view.empty


def test_meeting_banners(qapp, services, tmp_path):
    from workreport.ui.meeting_view import MeetingsView

    view = MeetingsView(services)
    wav = write_wav(tmp_path / "a.wav", tone(1))
    meeting = services.import_audio(wav)
    services.db.update_meeting(meeting.id, status=MeetingStatus.TRANSCRIBING, progress=0.42)
    view.refresh_list()
    view.select_meeting(meeting.id)
    assert view.detail.banner_kind == "info" and view.detail.banner_progress.value() == 42
    services.db.update_meeting(meeting.id, status=MeetingStatus.ERROR, error="모델 없음")
    view.on_meeting_changed(meeting.id)
    assert view.detail.banner_kind == "error" and view.detail.banner_text.text() == "모델 없음"


def test_recording_from_sidebar(qapp, services):
    from workreport.ui.main_window import MainWindow

    win = MainWindow(services)
    win.toggle_recording()
    assert services.recorder.is_recording
    win.refresh_recording_state()
    assert win.sidebar.record_btn.property("variant") == "recording"
    assert win.meetings.btn_record.property("variant") == "recording"
    time.sleep(0.3)
    win.toggle_recording()
    assert not services.recorder.is_recording
    assert len(services.db.list_meetings()) == 1
    assert win.meetings.current_id == services.db.list_meetings()[0].id
    win.sidebar.track_switch.setChecked(False)
    assert services.paused
    win.quitting = True
    win.close()


def test_settings_view_save(qapp, services):
    from workreport import credentials
    from workreport.config import CategoryRule
    from workreport.ui.settings_view import SettingsView

    view = SettingsView(services)
    themes = []
    view.theme_changed.connect(themes.append)
    assert not view.dirty
    view.user_name.setText("김영희")
    view.user_name.textEdited.emit("김영희")
    assert view.dirty
    view.rules.set_rules([CategoryRule(field="title", pattern="PROJ-\\d+", category="PROJ")])
    view.rules.add_rule(CategoryRule(field="app", pattern="Excel", category="문서"))
    view.rules.add_rule(CategoryRule(field="title", pattern="", category="빈 규칙"))
    view.select_engine("azure")
    assert not view.azure_card.isHidden() and view.whisper_card.isHidden()
    view.claude_key.setText("sk-ant-test")
    view.theme.set_current("dark")
    view.save()
    s = services.store.get()
    assert s.user_name == "김영희" and s.stt_engine == "azure" and s.theme == "dark"
    assert [(r.field, r.category) for r in s.category_rules] == [("title", "PROJ"), ("app", "문서")]
    assert credentials.get_secret(credentials.ANTHROPIC_API_KEY) == "sk-ant-test"
    assert view.claude_key.text() == "" and not view.dirty
    assert themes == ["dark"]
    for key in ("general", "activity", "meeting", "stt", "ai", "privacy"):
        view.show_page(key)


def test_history_view(qapp, services):
    from workreport.models import DailyReport
    from workreport.ui.history_view import HistoryView

    services.db.save_report(DailyReport(date=TODAY, summary="요약", accomplishments=[ReportItem(title="로그인 오류 수정")], status="final"))
    view = HistoryView(services)
    view.select_today()
    view.refresh()
    assert TODAY in view.calendar.marks
    assert "로그인 오류 수정" in view.preview.toPlainText()
    assert view.day_chip.text() == "확정됨"
    assert view.stat_reports.value.text() == "1일"


def test_onboarding_dialog(qapp, tmp_path):
    from workreport import credentials
    from workreport.ui.onboarding import OnboardingDialog

    store = SettingsStore(tmp_path / "c.json")
    dlg = OnboardingDialog(store)
    dlg._go(1)
    dlg.name.setText("박민수")
    dlg._go(1)
    dlg.api_key.setText("sk-ant-onboard")
    assert dlg.btn_next.text() == "시작하기"
    dlg._go(1)
    s = SettingsStore(tmp_path / "c.json").get()
    assert s.onboarding_done and s.user_name == "박민수"
    assert credentials.get_secret(credentials.ANTHROPIC_API_KEY) == "sk-ant-onboard"


def test_theme_switch_repaints(qapp):
    from workreport.ui.theme import DARK, LIGHT, apply_theme, tokens

    apply_theme(qapp, "dark")
    assert tokens() is DARK
    apply_theme(qapp, "light")
    assert tokens() is LIGHT


def test_application_wiring(qapp, services):
    from workreport.app import Application

    app = Application(qapp, services, start_minimized=True)
    notes = []
    app.tray.notify = lambda title, message, on_click=None: notes.append(title)
    app.auto_draft(TODAY)
    wait_until(lambda: "업무일지 초안이 준비되었어요" in notes)
    assert services.db.get_report(TODAY).summary == "AI 요약"

    app._on_meeting_detected("주간회의")
    assert notes[-1] == "회의 중인가요?"

    services.pipeline.start()
    meeting = services.import_audio(_wav())
    wait_until(lambda: services.db.get_meeting(meeting.id).status == MeetingStatus.DONE)
    wait_until(lambda: "회의록 준비 완료" in notes)
    app.open_ai_settings()
    assert app.window.current_page() == "settings"
    app.window.quitting = True
    app.timer.stop()
    app.tray.hide()
    services.stop()


def _wav():
    from workreport import paths

    return write_wav(paths.audio_dir() / "import.wav", tone(1))


def test_state_refresh_does_not_leak_listeners(qapp, services):
    from PySide6.QtCore import QObject

    from workreport.ui.main_window import MainWindow

    win = MainWindow(services)
    before = len(win.sidebar.record_btn.findChildren(QObject))
    for _ in range(20):
        win.refresh_recording_state()
    QApplication.sendPostedEvents(None, 0)
    assert len(win.sidebar.record_btn.findChildren(QObject)) <= before
    win.quitting = True
    win.close()
