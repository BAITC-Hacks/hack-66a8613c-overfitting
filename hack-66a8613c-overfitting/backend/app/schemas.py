"""Contracts for future processing and editing; no fabricated model output."""
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra='forbid')


class JobStatus(StrEnum):
    queued = 'queued'
    preparing_audio = 'preparing_audio'
    ready_for_models = 'ready_for_models'
    failed = 'failed'


class Meeting(Schema):
    id: str
    title: str = Field(min_length=1)
    meeting_date: date | None = None
    timezone: str | None = None
    participants: list[str] = Field(default_factory=list)
    summary: str = ''
    machine_prepared: bool = True
    approved_at: datetime | None = None

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError('Expected an IANA timezone') from exc
        return value


class SourceFile(Schema):
    id: str
    meeting_id: str
    original_name: str
    storage_path: str
    size_bytes: int = Field(ge=0, le=200 * 1024 * 1024)
    duration_seconds: float | None = Field(default=None, ge=0, le=900)


class ProcessingJob(Schema):
    id: str
    meeting_id: str
    status: JobStatus = JobStatus.queued
    stage: str | None = None
    error_code: str | None = None
    message: str = ''


class PreparationResult(Schema):
    meeting: Meeting
    job: ProcessingJob
    models_connected: bool = False


class Speaker(Schema):
    id: str
    meeting_id: str
    label: str
    name: str | None = None


class Utterance(Schema):
    id: str
    meeting_id: str
    speaker_id: str | None = None
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str
    requires_review: bool = False

    @model_validator(mode='after')
    def ordered_times(self):
        if self.end_seconds < self.start_seconds:
            raise ValueError('End precedes start')
        return self


class Topic(Schema):
    id: str
    meeting_id: str
    position: int = Field(ge=1)
    title: str
    summary: str


class Metric(Schema):
    id: str
    meeting_id: str
    topic_id: str
    utterance_id: str
    text: str


class Problem(Metric):
    pass


class ActionItem(Schema):
    id: str
    meeting_id: str
    topic_id: str
    text: str = Field(min_length=1)
    responsible: str = Field(default='не указан', min_length=1)
    deadline_original: str = Field(default='не указан', min_length=1)
    deadline_date: date | None = None
    utterance_id: str
    timestamp_seconds: float = Field(ge=0)
    requires_review: bool


class Export(Schema):
    id: str
    meeting_id: str
    format: str = Field(pattern='^(docx|pdf)$')
    storage_path: str
    created_at: datetime


class MeetingResult(Schema):
    meeting: Meeting
    speakers: list[Speaker]
    utterances: list[Utterance]
    topics: list[Topic]
    metrics: list[Metric]
    problems: list[Problem]
    action_items: list[ActionItem]

    @model_validator(mode='after')
    def dated_deadlines_have_context(self):
        if any(item.deadline_date is not None for item in self.action_items):
            if self.meeting.meeting_date is None or self.meeting.timezone is None:
                raise ValueError('Normalized deadlines require meeting date and timezone')
        return self


class MeetingEdits(Schema):
    speakers: list[Speaker] = Field(default_factory=list)
    utterances: list[Utterance] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    approve: bool = False
