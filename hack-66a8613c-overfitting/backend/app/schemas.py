"""Contracts for future processing and editing; no fabricated model output."""
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra='forbid')


class JobStatus(StrEnum):
    queued = 'queued'
    preparing_audio = 'preparing_audio'
    ready_for_models = 'ready_for_models'
    transcribing = 'transcribing'
    diarizing = 'diarizing'
    saving_transcript = 'saving_transcript'
    analyzing = 'analyzing'
    ready = 'ready'
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
    detected_language: str | None = None


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
    source_utterance_ids: list[str] = Field(default_factory=list)
    requires_review: bool = True


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
    source_utterance_ids: list[str] = Field(default_factory=list)


class KeyPoint(Schema):
    id: str
    meeting_id: str
    position: int
    direction: str
    metric: str
    problem: str
    source_utterance_ids: list[str] = Field(default_factory=list)
    requires_review: bool = True


class PreparationResult(Schema):
    meeting: Meeting
    job: ProcessingJob
    models_connected: bool = False
    detected_language: str | None = None
    speakers: list[Speaker] = Field(default_factory=list)
    utterances: list[Utterance] = Field(default_factory=list)
    summary: str = ''
    analysis_completed: bool = False
    requires_review: bool = True
    key_points: list[KeyPoint] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)


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


EditText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
EntityId = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class StrictEdit(Schema):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('*')
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and any(not (char in '\t\n\r' or '\u0020' <= char <= '\ud7ff'
                                             or '\ue000' <= char <= '\ufffd' or '\U00010000' <= char <= '\U0010ffff') for char in value):
            raise ValueError('Invalid text character')
        return value


class SpeakerEdit(StrictEdit):
    id: EntityId
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ActionItemEdit(StrictEdit):
    id: EntityId
    text: EditText | None = None
    responsible: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    deadline_original: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    requires_review: bool | None = None

    @model_validator(mode='after')
    def actual_changes(self):
        fields = self.model_fields_set - {'id'}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError('Expected non-null editable fields')
        return self


class MeetingEdits(StrictEdit):
    speakers: list[SpeakerEdit] = Field(default_factory=list, max_length=1000)
    action_items: list[ActionItemEdit] = Field(default_factory=list, max_length=10000)
    approve: bool = False

    @model_validator(mode='after')
    def unique_entities(self):
        for items in (self.speakers, self.action_items):
            if len({item.id for item in items}) != len(items):
                raise ValueError('Duplicate entity IDs')
        return self
