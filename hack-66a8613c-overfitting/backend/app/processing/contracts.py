"""Small validated adapter contracts; importing them needs no ML runtime."""
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from ..schemas import Schema


class Interval(Schema):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('Invalid interval')
        return self


class AsrSegment(Interval):
    text: str = Field(min_length=1)


class Transcription(Schema):
    segments: list[AsrSegment]
    # Initial recording-level language estimate, not a language assigned to every phrase.
    detected_language: str | None = Field(default=None, pattern=r'^[a-z]{2,3}$')


class DiarizationTurn(Interval):
    speaker: str = Field(min_length=1)


class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> Transcription: ...


class Diarizer(Protocol):
    def diarize(self, audio: Path) -> list[DiarizationTurn]: ...
