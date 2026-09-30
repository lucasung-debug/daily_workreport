"""SQLite 저장소.

연결 하나를 여러 스레드(수집기·파이프라인·UI)가 공유하므로 모든 접근을 RLock 으로 직렬화한다.
시각은 epoch 초(float), 날짜는 로컬 기준 'YYYY-MM-DD' 문자열로 저장한다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import (
    ActionItem,
    ActivitySession,
    DailyReport,
    DailyReportDraft,
    Meeting,
    MeetingMinutes,
    MeetingStatus,
    Note,
    ReportItem,
    Screenshot,
    Transcript,
)

_MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE activity_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_ts REAL NOT NULL,
        end_ts REAL NOT NULL,
        app_name TEXT NOT NULL,
        exe_path TEXT NOT NULL DEFAULT '',
        window_title TEXT NOT NULL DEFAULT '',
        is_idle INTEGER NOT NULL DEFAULT 0,
        category TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_sessions_start ON activity_sessions(start_ts);

    CREATE TABLE screenshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        app_name TEXT NOT NULL DEFAULT '',
        window_title TEXT NOT NULL DEFAULT '',
        image_path TEXT NOT NULL DEFAULT '',
        caption TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending'
    );
    CREATE INDEX idx_screenshots_ts ON screenshots(ts);

    CREATE TABLE notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        ts REAL NOT NULL,
        text TEXT NOT NULL
    );
    CREATE INDEX idx_notes_date ON notes(date);

    CREATE TABLE daily_reports (
        date TEXT PRIMARY KEY,
        summary TEXT NOT NULL DEFAULT '',
        accomplishments_json TEXT NOT NULL DEFAULT '[]',
        plans_json TEXT NOT NULL DEFAULT '[]',
        issues_json TEXT NOT NULL DEFAULT '[]',
        memo TEXT NOT NULL DEFAULT '',
        ai_draft_json TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        started_at REAL NOT NULL,
        ended_at REAL NOT NULL,
        source TEXT NOT NULL,
        audio_path TEXT NOT NULL DEFAULT '',
        stt_engine TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        progress REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        transcript_json TEXT,
        minutes_json TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX idx_meetings_date ON meetings(date);

    CREATE TABLE action_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        owner TEXT NOT NULL DEFAULT '',
        task TEXT NOT NULL,
        due TEXT NOT NULL DEFAULT '',
        is_mine INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_action_items_meeting ON action_items(meeting_id);

    CREATE TABLE processed_files (
        path TEXT PRIMARY KEY,
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        meeting_id INTEGER
    );
    """,
]


def day_bounds(day: str | date) -> tuple[float, float]:
    """로컬 날짜의 [00:00, 다음날 00:00) epoch 범위."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(d, datetime.min.time())
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def local_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).date().isoformat()


def _items_json(items: Iterable[ReportItem]) -> str:
    return json.dumps([i.model_dump() for i in items], ensure_ascii=False)


def _items_from(raw: str | None) -> list[ReportItem]:
    if not raw:
        return []
    return [ReportItem.model_validate(i) for i in json.loads(raw)]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    # ------------------------------------------------------------ infra
    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for idx, script in enumerate(_MIGRATIONS[version:], start=version + 1):
            with self._conn:
                self._conn.executescript(script)
                self._conn.execute(f"PRAGMA user_version = {idx}")

    @property
    def schema_version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, tuple(params))

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ activity
    def insert_session(self, s: ActivitySession) -> int:
        cur = self._exec(
            "INSERT INTO activity_sessions(start_ts, end_ts, app_name, exe_path, window_title, is_idle, category)"
            " VALUES (?,?,?,?,?,?,?)",
            (s.start_ts, s.end_ts, s.app_name, s.exe_path, s.window_title, int(s.is_idle), s.category),
        )
        s.id = cur.lastrowid
        return s.id

    def update_session_end(self, session_id: int, end_ts: float) -> None:
        self._exec("UPDATE activity_sessions SET end_ts=? WHERE id=?", (end_ts, session_id))

    def sessions_between(self, start_ts: float, end_ts: float) -> list[ActivitySession]:
        rows = self._all(
            "SELECT * FROM activity_sessions WHERE end_ts > ? AND start_ts < ? ORDER BY start_ts",
            (start_ts, end_ts),
        )
        return [
            ActivitySession(
                id=r["id"],
                start_ts=max(r["start_ts"], start_ts),
                end_ts=min(r["end_ts"], end_ts),
                app_name=r["app_name"],
                exe_path=r["exe_path"],
                window_title=r["window_title"],
                is_idle=bool(r["is_idle"]),
                category=r["category"],
            )
            for r in rows
        ]

    def sessions_for_date(self, day: str) -> list[ActivitySession]:
        return self.sessions_between(*day_bounds(day))

    # ------------------------------------------------------------ notes
    def add_note(self, text: str, ts: float | None = None) -> Note:
        ts = ts or time.time()
        note = Note(date=local_date(ts), ts=ts, text=text)
        note.id = self._exec("INSERT INTO notes(date, ts, text) VALUES (?,?,?)", (note.date, ts, text)).lastrowid
        return note

    def notes_for(self, day: str) -> list[Note]:
        rows = self._all("SELECT * FROM notes WHERE date=? ORDER BY ts", (day,))
        return [Note(id=r["id"], date=r["date"], ts=r["ts"], text=r["text"]) for r in rows]

    def delete_note(self, note_id: int) -> None:
        self._exec("DELETE FROM notes WHERE id=?", (note_id,))

    # ------------------------------------------------------------ reports
    def get_report(self, day: str) -> DailyReport | None:
        r = self._one("SELECT * FROM daily_reports WHERE date=?", (day,))
        if not r:
            return None
        return DailyReport(
            date=r["date"],
            summary=r["summary"],
            accomplishments=_items_from(r["accomplishments_json"]),
            plans=_items_from(r["plans_json"]),
            issues=_items_from(r["issues_json"]),
            memo=r["memo"],
            ai_draft=DailyReportDraft.model_validate_json(r["ai_draft_json"]) if r["ai_draft_json"] else None,
            status=r["status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def save_report(self, report: DailyReport) -> None:
        now = time.time()
        report.created_at = report.created_at or now
        report.updated_at = now
        self._exec(
            """
            INSERT INTO daily_reports(date, summary, accomplishments_json, plans_json, issues_json, memo,
                                      ai_draft_json, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                summary=excluded.summary, accomplishments_json=excluded.accomplishments_json,
                plans_json=excluded.plans_json, issues_json=excluded.issues_json, memo=excluded.memo,
                ai_draft_json=excluded.ai_draft_json, status=excluded.status, updated_at=excluded.updated_at
            """,
            (
                report.date,
                report.summary,
                _items_json(report.accomplishments),
                _items_json(report.plans),
                _items_json(report.issues),
                report.memo,
                report.ai_draft.model_dump_json() if report.ai_draft else None,
                report.status,
                report.created_at,
                report.updated_at,
            ),
        )

    def report_dates(self) -> dict[str, str]:
        """{날짜: 상태} — 달력 표시용."""
        return {r["date"]: r["status"] for r in self._all("SELECT date, status FROM daily_reports")}

    def previous_report(self, day: str) -> DailyReport | None:
        r = self._one("SELECT date FROM daily_reports WHERE date < ? ORDER BY date DESC LIMIT 1", (day,))
        return self.get_report(r["date"]) if r else None

    # ------------------------------------------------------------ meetings
    def _meeting_from(self, r: sqlite3.Row) -> Meeting:
        return Meeting(
            id=r["id"],
            date=r["date"],
            title=r["title"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            source=r["source"],
            audio_path=r["audio_path"],
            stt_engine=r["stt_engine"],
            status=r["status"],
            progress=r["progress"],
            error=r["error"],
            transcript=Transcript.model_validate_json(r["transcript_json"]) if r["transcript_json"] else None,
            minutes=MeetingMinutes.model_validate_json(r["minutes_json"]) if r["minutes_json"] else None,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def create_meeting(self, m: Meeting) -> int:
        now = time.time()
        m.created_at = m.updated_at = now
        m.id = self._exec(
            "INSERT INTO meetings(date, title, started_at, ended_at, source, audio_path, stt_engine, status, progress,"
            " error, transcript_json, minutes_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                m.date,
                m.title,
                m.started_at,
                m.ended_at,
                m.source,
                m.audio_path,
                m.stt_engine,
                m.status,
                m.progress,
                m.error,
                m.transcript.model_dump_json() if m.transcript else None,
                m.minutes.model_dump_json() if m.minutes else None,
                now,
                now,
            ),
        ).lastrowid
        return m.id

    _MEETING_FIELDS = {"title", "started_at", "ended_at", "audio_path", "stt_engine", "status", "progress", "error", "date"}

    def update_meeting(self, meeting_id: int, **fields: Any) -> None:
        cols, vals = [], []
        for key, value in fields.items():
            if key == "transcript":
                cols.append("transcript_json=?")
                vals.append(value.model_dump_json() if value else None)
            elif key == "minutes":
                cols.append("minutes_json=?")
                vals.append(value.model_dump_json() if value else None)
            elif key in self._MEETING_FIELDS:
                cols.append(f"{key}=?")
                vals.append(value)
            else:
                raise KeyError(key)
        cols.append("updated_at=?")
        vals.append(time.time())
        self._exec(f"UPDATE meetings SET {', '.join(cols)} WHERE id=?", (*vals, meeting_id))

    def get_meeting(self, meeting_id: int) -> Meeting | None:
        r = self._one("SELECT * FROM meetings WHERE id=?", (meeting_id,))
        return self._meeting_from(r) if r else None

    def meetings_for(self, day: str) -> list[Meeting]:
        return [self._meeting_from(r) for r in self._all("SELECT * FROM meetings WHERE date=? ORDER BY started_at", (day,))]

    def list_meetings(self, limit: int = 200) -> list[Meeting]:
        rows = self._all("SELECT * FROM meetings ORDER BY started_at DESC LIMIT ?", (limit,))
        return [self._meeting_from(r) for r in rows]

    def pending_meetings(self) -> list[Meeting]:
        marks = ",".join("?" * len(MeetingStatus.PENDING))
        rows = self._all(f"SELECT * FROM meetings WHERE status IN ({marks}) ORDER BY id", MeetingStatus.PENDING)
        return [self._meeting_from(r) for r in rows]

    def delete_meeting(self, meeting_id: int) -> None:
        self._exec("DELETE FROM meetings WHERE id=?", (meeting_id,))

    def meetings_with_audio_before(self, ts: float) -> list[Meeting]:
        rows = self._all("SELECT * FROM meetings WHERE audio_path != '' AND ended_at < ?", (ts,))
        return [self._meeting_from(r) for r in rows]

    # ------------------------------------------------------------ action items
    def replace_action_items(self, meeting_id: int, items: Iterable[ActionItem]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM action_items WHERE meeting_id=?", (meeting_id,))
            for item in items:
                item.meeting_id = meeting_id
                item.id = self._conn.execute(
                    "INSERT INTO action_items(meeting_id, owner, task, due, is_mine, done) VALUES (?,?,?,?,?,?)",
                    (meeting_id, item.owner, item.task, item.due, int(item.is_mine), int(item.done)),
                ).lastrowid

    @staticmethod
    def _action_from(r: sqlite3.Row) -> ActionItem:
        return ActionItem(
            id=r["id"],
            meeting_id=r["meeting_id"],
            owner=r["owner"],
            task=r["task"],
            due=r["due"],
            is_mine=bool(r["is_mine"]),
            done=bool(r["done"]),
        )

    def action_items_for(self, meeting_id: int) -> list[ActionItem]:
        return [self._action_from(r) for r in self._all("SELECT * FROM action_items WHERE meeting_id=? ORDER BY id", (meeting_id,))]

    def open_my_action_items(self) -> list[tuple[ActionItem, Meeting]]:
        rows = self._all(
            "SELECT a.* FROM action_items a JOIN meetings m ON m.id = a.meeting_id"
            " WHERE a.is_mine=1 AND a.done=0 ORDER BY CASE WHEN a.due='' THEN 1 ELSE 0 END, a.due, m.started_at"
        )
        out = []
        for r in rows:
            item = self._action_from(r)
            meeting = self.get_meeting(item.meeting_id)
            if meeting:
                out.append((item, meeting))
        return out

    def set_action_item_done(self, item_id: int, done: bool) -> None:
        self._exec("UPDATE action_items SET done=? WHERE id=?", (int(done), item_id))

    # ------------------------------------------------------------ processed files
    def is_processed(self, path: str) -> bool:
        return self._one("SELECT 1 FROM processed_files WHERE path=?", (path,)) is not None

    def mark_processed(self, path: str, size: int, mtime: float, meeting_id: int | None) -> None:
        self._exec(
            "INSERT OR REPLACE INTO processed_files(path, size, mtime, meeting_id) VALUES (?,?,?,?)",
            (path, size, mtime, meeting_id),
        )

    # ------------------------------------------------------------ screenshots
    def add_screenshot(self, s: Screenshot) -> int:
        s.id = self._exec(
            "INSERT INTO screenshots(ts, app_name, window_title, image_path, caption, status) VALUES (?,?,?,?,?,?)",
            (s.ts, s.app_name, s.window_title, s.image_path, s.caption, s.status),
        ).lastrowid
        return s.id

    def update_screenshot(self, screenshot_id: int, **fields: Any) -> None:
        allowed = {"image_path", "caption", "status"}
        cols = [f"{k}=?" for k in fields if k in allowed]
        if len(cols) != len(fields):
            raise KeyError(set(fields) - allowed)
        self._exec(f"UPDATE screenshots SET {', '.join(cols)} WHERE id=?", (*fields.values(), screenshot_id))

    def screenshots_between(self, start_ts: float, end_ts: float) -> list[Screenshot]:
        rows = self._all("SELECT * FROM screenshots WHERE ts >= ? AND ts < ? ORDER BY ts", (start_ts, end_ts))
        return [
            Screenshot(
                id=r["id"],
                ts=r["ts"],
                app_name=r["app_name"],
                window_title=r["window_title"],
                image_path=r["image_path"],
                caption=r["caption"],
                status=r["status"],
            )
            for r in rows
        ]
