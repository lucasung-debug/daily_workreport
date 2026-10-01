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
    services.todos.add("주간 보고 정리")
    editor = ReportEditor(services)
    editor.reload_side()
    panel = editor.todo_panel
    assert panel.compact and len(panel.rows) == 3  # 홍길동(본인) + '나' 담당 + 직접 추가
    action = next(r.entry for r in panel.rows if r.entry.kind == "action")
    panel.rows[[r.entry for r in panel.rows].index(action)].btn_plan.click()
    added = editor.plans.items()[-1]
    assert added.title == action.title and added.category == "액션아이템"
    assert added.detail.startswith("회의 · ")
    assert panel.caption_label.text() == "3개"
    editor.flush()


def test_dashboard_todo_panel(qapp, services):
    from datetime import timedelta

    from workreport.models import DailyReport
    from workreport.ui.main_window import MainWindow

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    services.db.save_report(DailyReport(date=yesterday, plans=[ReportItem(title="API 문서 정리"), ReportItem(title="코드 리뷰")]))
    win = MainWindow(services)
    win.show()
    win.go("today")
    pump()
    panel = win.today.todo_panel
    body = win.today.findChild(type(panel)).parentWidget().layout()
    assert body.indexOf(panel) == 1  # 인사 바로 아래, 지표 카드보다 위
    assert panel.open_titles() == ["API 문서 정리", "코드 리뷰"]
    assert panel.rows[0].source.text() == "어제 계획"
    assert win.sidebar.today_badge.text() == "2" and not win.sidebar.today_badge.isHidden()

    # 빠른 추가: 기한·중요
    panel.input.setText("릴리스 공지 보내기")
    panel.set_new_due(TODAY)
    panel.btn_new_important.setChecked(True)
    panel.input.returnPressed.emit()
    assert panel.input.text() == "" and panel.new_due == "" and not panel.btn_new_important.isChecked()
    assert panel.open_titles()[0] == "릴리스 공지 보내기"  # 오늘 기한 + 중요 → 맨 위
    assert panel.rows[0].due_chip.text() == "오늘까지"
    wait_until(lambda: win.sidebar.today_badge.text() == "3")

    # 바로 편집
    row = panel.rows[2]
    row.title.setText("코드 리뷰 (PR 42)")
    row.title.editingFinished.emit()
    wait_until(lambda: "코드 리뷰 (PR 42)" in panel.open_titles())

    # 완료 → 잠깐 뒤 '완료한 일'로 이동, 진행률 갱신
    panel.rows[0].check.setChecked(True)
    wait_until(lambda: "릴리스 공지 보내기" not in panel.open_titles())
    assert panel.progress_label.text() == "1 / 3 완료" and panel.progress.value() == 1
    assert panel.done_toggle.text() == "완료한 일 1개" and panel.done_titles() == []
    panel.done_toggle.click()
    assert panel.done_titles() == ["릴리스 공지 보내기"]
    assert panel.done_rows[0].title.property("done") is True
    wait_until(lambda: win.sidebar.today_badge.text() == "2")

    # 삭제한 어제 계획은 다시 들어오지 않는다
    plan_row = next(r for r in panel.rows if r.entry.title == "API 문서 정리")
    plan_row.delete_requested.emit(plan_row.entry)
    wait_until(lambda: "API 문서 정리" not in panel.open_titles())
    assert "지웠어요" in win.toast.message
    win.go("today")
    assert "API 문서 정리" not in panel.open_titles()

    # 모두 끝내면 빈 상태
    for title in list(panel.open_titles()):
        row = next(r for r in panel.rows if r.entry.title == title)
        row.check.setChecked(True)
    wait_until(lambda: panel.open_titles() == [])
    assert win.sidebar.today_badge.isHidden()
    assert panel.caption_label.isHidden()
    win.quitting = True
    win.close()


def test_todo_panel_more_and_order(qapp, services):
    from workreport.ui.todo_view import MAX_OPEN, TodoPanel

    for i in range(MAX_OPEN + 2):
        services.todos.add(f"할 일 {i}")
    panel = TodoPanel(services)
    assert len(panel.rows) == MAX_OPEN and panel.more_btn.text() == "남은 할 일 2개 더 보기"
    panel.more_btn.click()
    assert len(panel.rows) == MAX_OPEN + 2 and panel.more_btn.text() == "접기"
    last = panel.rows[-1]
    last.important_toggled.emit(last.entry)
    wait_until(lambda: panel.open_titles()[0] == f"할 일 {MAX_OPEN + 1}")
    assert panel.rows[0].star.isChecked()


def test_todo_action_items_stay_in_sync_with_meeting(qapp, services, tmp_path):
    from workreport.ui.meeting_view import MeetingsView
    from workreport.ui.todo_view import TodoPanel

    meeting = services.import_audio(write_wav(tmp_path / "주간회의.wav", tone(1)))
    services.pipeline.process(meeting.id)
    panel = TodoPanel(services)
    view = MeetingsView(services)
    view.refresh_list()
    view.select_meeting(meeting.id)
    assert sorted(panel.open_titles()) == sorted(["배포 체크리스트", "릴리스 노트 작성"])
    row = next(r for r in panel.rows if r.entry.title == "배포 체크리스트")
    assert row.source.text().startswith("회의 · ") and row.title.isReadOnly()
    assert not row.star.isEnabled() and row.hover_buttons == []

    # 오늘 화면에서 체크 → 회의록의 체크도 바뀐다
    row.check.setChecked(True)
    meeting_row = next(r for r in view.detail.action_rows if r.task.text() == "배포 체크리스트")
    wait_until(lambda: meeting_row.done.isChecked())
    wait_until(lambda: "배포 체크리스트" not in panel.open_titles())
    assert not view.save_timer.isActive()  # 맞추기만 하고 다시 저장하지 않는다

    # 회의록에서 체크 해제 → 할 일 목록으로 돌아온다
    meeting_row.done.setChecked(False)
    wait_until(lambda: not view.save_timer.isActive())
    wait_until(lambda: "배포 체크리스트" in panel.open_titles())

    # 액션아이템은 할 일 목록에서 지울 수 없다
    entry = next(r.entry for r in panel.rows if r.entry.kind == "action")
    messages = []
    panel.status_message.connect(lambda m, _k: messages.append(m))
    panel._delete(entry)
    assert messages and "회의록" in messages[-1]


def test_due_helpers():
    from workreport.todos import TodoEntry
    from workreport.ui.todo_view import due_label, due_options, due_text

    thursday, monday = date(2026, 10, 1), date(2026, 9, 28)
    assert [v for _l, v in due_options(thursday)] == ["2026-10-01", "2026-10-02", "2026-10-05"]
    assert [label.split()[0] for label, _v in due_options(monday)] == ["오늘", "내일", "이번", "다음"]
    assert due_options(monday)[2][1] == "2026-10-02"
    entry = TodoEntry(kind="manual", id=1, title="x")
    assert due_text(entry, "2026-10-01") == ("", "")
    for due, expected in [("2026-09-29", ("9/29 지남", "danger")), ("2026-10-01", ("오늘까지", "warning")), ("2026-10-02", ("내일까지", "accent")), ("2026-10-08", ("10/8까지", "neutral"))]:
        entry.due = due
        assert due_text(entry, "2026-10-01") == expected
    assert due_label("", thursday) == "기한" and due_label("2026-10-02", thursday) == "내일" and due_label("2026-10-05", thursday) == "10/5 (월)"


def test_tray_quick_todo(qapp, services, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from workreport.ui.tray import Tray

    tray = Tray(services, on_open=lambda: None, on_toggle_recording=lambda: None, on_generate=lambda: None, on_quit=lambda: None)
    notes = []
    tray.notify = lambda title, message, on_click=None: notes.append(title)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("  보고서 검토 ", True)))
    tray.quick_todo()
    assert [e.title for e in services.todos.open_entries()] == ["보고서 검토"] and notes == ["할 일 추가"]
    tray.timer.stop()
    tray.hide()


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
