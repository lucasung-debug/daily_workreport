"""도메인 모델.

- pydantic 모델: Claude 구조화 출력 스키마와 저장(JSON 컬럼)에 함께 쓰인다.
- dataclass: DB 행.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 업무일지


class ReportItem(BaseModel):
    title: str = Field(description="항목 제목 (한 줄, 명사형 종결)")
    detail: str = Field(default="", description="세부 내용. 없으면 빈 문자열")
    time_spent_min: int = Field(default=0, description="투입 시간(분). 모르면 0")
    category: str = Field(default="", description="프로젝트/업무 분류. 없으면 빈 문자열")


class DailyReportDraft(BaseModel):
    summary: str = Field(description="오늘 업무 한 줄 요약")
    accomplishments: list[ReportItem] = Field(description="금일 실적")
    plans: list[ReportItem] = Field(description="명일 계획")
    issues: list[ReportItem] = Field(description="이슈·협조 요청 사항. 없으면 빈 배열")


# ---------------------------------------------------------------- 회의


class Segment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str = ""


class Transcript(BaseModel):
    engine: str = ""
    language: str = "ko"
    duration_sec: float = 0.0
    segments: list[Segment] = Field(default_factory=list)

    def to_text(self, with_timestamps: bool = True) -> str:
        lines = []
        for seg in self.segments:
            prefix = f"[{format_hms(seg.start)}] " if with_timestamps else ""
            speaker = f"{seg.speaker}: " if seg.speaker else ""
            lines.append(f"{prefix}{speaker}{seg.text.strip()}")
        return "\n".join(lines)


class DiscussionTopic(BaseModel):
    topic: str = Field(description="논의 주제")
    points: list[str] = Field(description="주요 논의 내용")


class ActionItemDraft(BaseModel):
    owner: str = Field(description="담당자 이름. 불명확하면 빈 문자열")
    task: str = Field(description="할 일")
    due: str = Field(description="기한 YYYY-MM-DD. 언급이 없으면 빈 문자열")
    is_mine: bool = Field(description="기록자 본인이 담당하는 일이면 true")


class MeetingMinutes(BaseModel):
    title: str = Field(description="회의 제목")
    summary: str = Field(description="회의 요약 (2~4문장)")
    attendees: list[str] = Field(description="발언·언급으로 확인되는 참석자. 모르면 빈 배열")
    discussion: list[DiscussionTopic] = Field(description="주제별 논의 내용")
    decisions: list[str] = Field(description="결정 사항")
    action_items: list[ActionItemDraft] = Field(description="액션 아이템")
    open_questions: list[str] = Field(description="미결 사항·추가 확인 필요 사항")


# ---------------------------------------------------------------- DB 행


@dataclass
class ActivitySession:
    start_ts: float
    end_ts: float
    app_name: str
    window_title: str
    exe_path: str = ""
    is_idle: bool = False
    category: str = ""
    id: int | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)


@dataclass
class Note:
    date: str
    ts: float
    text: str
    id: int | None = None


@dataclass
class DailyReport:
    date: str
    summary: str = ""
    accomplishments: list[ReportItem] = field(default_factory=list)
    plans: list[ReportItem] = field(default_factory=list)
    issues: list[ReportItem] = field(default_factory=list)
    memo: str = ""
    ai_draft: DailyReportDraft | None = None
    status: str = "draft"  # draft | final
    created_at: float = 0.0
    updated_at: float = 0.0


class MeetingStatus:
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"  # 전사 완료, 회의록 요약 대기(Claude 미설정 등)
    SUMMARIZING = "summarizing"
    DONE = "done"
    ERROR = "error"

    PENDING = (QUEUED, TRANSCRIBING, SUMMARIZING)
    LABELS = {
        QUEUED: "대기",
        TRANSCRIBING: "음성 변환 중",
        TRANSCRIBED: "변환 완료(요약 대기)",
        SUMMARIZING: "회의록 작성 중",
        DONE: "완료",
        ERROR: "오류",
    }


@dataclass
class Meeting:
    date: str
    title: str
    started_at: float
    ended_at: float
    source: str  # recorder_app | in_app | import
    audio_path: str
    status: str = MeetingStatus.QUEUED
    progress: float = 0.0
    error: str = ""
    stt_engine: str = ""
    transcript: Transcript | None = None
    minutes: MeetingMinutes | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    id: int | None = None

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.ended_at - self.started_at)


@dataclass
class ActionItem:
    meeting_id: int
    task: str
    owner: str = ""
    due: str = ""
    is_mine: bool = False
    done: bool = False
    id: int | None = None


@dataclass
class Screenshot:
    ts: float
    app_name: str
    window_title: str
    image_path: str = ""
    caption: str = ""
    status: str = "pending"  # pending | done | error | skipped
    id: int | None = None


def format_hms(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
