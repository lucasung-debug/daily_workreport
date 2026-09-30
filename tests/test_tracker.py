from datetime import datetime

from workreport.collector.base import WindowInfo
from workreport.collector.fake import FakeProbe
from workreport.collector.rules import MASKED_TITLE, categorize, is_excluded, within_tracking_hours
from workreport.collector.screenshot import ScreenshotService
from workreport.collector.tracker import IDLE_APP, LOCKED_TITLE, ActivityTracker
from workreport.config import CategoryRule, Settings

BASE = datetime(2026, 9, 30, 10, 0, 0).timestamp()


class Clock:
    def __init__(self, t=BASE):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


def make(db, settings=None):
    probe = FakeProbe(WindowInfo("Visual Studio Code", "main.py - project", "Code.exe"))
    clock = Clock()
    s = settings or Settings()
    tracker = ActivityTracker(db, probe, lambda: s, clock=clock)
    return tracker, probe, clock


def sessions(db):
    return db.sessions_between(BASE - 86400, BASE + 86400)


def test_same_window_extends_one_session(db):
    tracker, probe, clock = make(db)
    for _ in range(5):
        tracker.sample()
        clock.advance(2)
    rows = sessions(db)
    assert len(rows) == 1
    assert rows[0].duration == 8
    assert rows[0].app_name == "Visual Studio Code"


def test_window_change_is_contiguous(db):
    tracker, probe, clock = make(db)
    tracker.sample()
    clock.advance(2)
    tracker.sample()
    clock.advance(2)
    probe.window = WindowInfo("Microsoft Excel", "예산.xlsx", "EXCEL.EXE")
    tracker.sample()
    rows = sessions(db)
    assert [r.app_name for r in rows] == ["Visual Studio Code", "Microsoft Excel"]
    assert rows[0].end_ts == rows[1].start_ts


def test_idle_backdates_to_last_input(db):
    tracker, probe, clock = make(db, Settings(idle_threshold_sec=300))
    tracker.sample()
    for _ in range(200):  # 400초 경과, 마지막 입력은 시작 시점
        clock.advance(2)
        probe.idle = clock.t - BASE
        tracker.sample()
    rows = sessions(db)
    assert [r.is_idle for r in rows] == [False, True]
    assert rows[0].end_ts == BASE  # 활동 세션은 마지막 입력 시각에서 잘린다
    assert rows[1].start_ts == BASE
    assert rows[1].app_name == IDLE_APP

    probe.idle = 0  # 복귀
    clock.advance(2)
    tracker.sample()
    assert [r.is_idle for r in sessions(db)] == [False, True, False]


def test_locked_screen_is_idle(db):
    tracker, probe, clock = make(db)
    probe.locked = True
    tracker.sample()
    row = sessions(db)[0]
    assert row.is_idle and row.window_title == LOCKED_TITLE


def test_sleep_gap_breaks_session(db):
    tracker, probe, clock = make(db)
    tracker.sample()
    clock.advance(3600)
    tracker.sample()
    rows = sessions(db)
    assert len(rows) == 2
    assert rows[0].duration == 0
    assert rows[1].start_ts == clock.t


def test_pause_stops_recording(db):
    tracker, probe, clock = make(db)
    tracker.pause()
    tracker.sample()
    assert sessions(db) == []
    tracker.resume()
    tracker.sample()
    assert len(sessions(db)) == 1


def test_excluded_title_is_masked(db):
    tracker, probe, clock = make(db)
    probe.window = WindowInfo("Microsoft Edge", "KB국민 인터넷뱅킹 - InPrivate", "msedge.exe")
    tracker.sample()
    row = sessions(db)[0]
    assert row.window_title == MASKED_TITLE and row.app_name == "Microsoft Edge"


def test_excluded_app():
    s = Settings(excluded_apps=["KakaoTalk.exe"])
    assert is_excluded(WindowInfo("카카오톡", "채팅", "KakaoTalk.exe"), s)
    assert not is_excluded(WindowInfo("메모장", "a.txt", "notepad.exe"), s)


def test_category_rules(db):
    rules = [CategoryRule(field="title", pattern=r"\[PROJ-\d+\]", category="PROJ"), CategoryRule(field="app", pattern="excel", category="문서")]
    assert categorize("Chrome", "[PROJ-12] 버그 - Jira", rules) == "PROJ"
    assert categorize("Microsoft Excel", "a.xlsx", rules) == "문서"
    assert categorize("메모장", "x", rules) == ""
    assert categorize("x", "y", [CategoryRule(pattern="(", category="bad")]) == ""


def test_tracking_hours_window():
    s = Settings(work_start="09:00", work_end="18:00")
    assert within_tracking_hours(datetime(2026, 9, 30, 8, 45), s)
    assert within_tracking_hours(datetime(2026, 9, 30, 20, 30), s)
    assert not within_tracking_hours(datetime(2026, 9, 30, 7, 0), s)
    assert not within_tracking_hours(datetime(2026, 9, 30, 23, 0), s)
    assert within_tracking_hours(datetime(2026, 9, 30, 23, 0), Settings(track_outside_work_hours=True))


def test_outside_hours_not_recorded(db):
    s = Settings(work_start="11:00", work_end="12:00")  # BASE=10:00 은 30분 여유 밖
    tracker, probe, clock = make(db, s)
    tracker.sample()
    assert sessions(db) == []


def fake_capture(dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"jpeg")
    return dest


def test_screenshot_disabled_by_default(db):
    probe = FakeProbe(WindowInfo("Excel", "a.xlsx", "EXCEL.EXE"))
    svc = ScreenshotService(db, probe, lambda: Settings(), lambda: (lambda p, a, t: "캡션"), capture=fake_capture)
    assert svc.tick() is None


def test_screenshot_caption_and_image_deleted(db):
    s = Settings(screenshot_enabled=True, track_outside_work_hours=True)
    probe = FakeProbe(WindowInfo("Excel", "a.xlsx", "EXCEL.EXE"))
    svc = ScreenshotService(db, probe, lambda: s, lambda: (lambda p, a, t: f"{a}에서 예산 작성"), capture=fake_capture)
    shot = svc.tick()
    assert shot.caption == "Excel에서 예산 작성" and shot.status == "done"
    assert shot.image_path == ""
    stored = db.screenshots_between(0, 2e10)
    assert stored[0].caption == "Excel에서 예산 작성"


def test_screenshot_skips_excluded_and_no_claude(db):
    s = Settings(screenshot_enabled=True, track_outside_work_hours=True, excluded_title_keywords=["뱅킹"])
    probe = FakeProbe(WindowInfo("Edge", "인터넷뱅킹", "msedge.exe"))
    assert ScreenshotService(db, probe, lambda: s, lambda: (lambda *a: "x"), capture=fake_capture).tick() is None
    probe.window = WindowInfo("Edge", "뉴스", "msedge.exe")
    assert ScreenshotService(db, probe, lambda: s, lambda: None, capture=fake_capture).tick() is None
