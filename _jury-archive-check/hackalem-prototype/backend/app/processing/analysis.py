"""Validated analysis contract and evidence checks, independent of the LLM client."""
from typing import Annotated, Protocol

from pydantic import ConfigDict, Field, StringConstraints

from ..errors import LocalError
from ..schemas import Schema, Speaker, Utterance

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
Sources = Annotated[list[Text], Field(min_length=1, max_length=500)]


class AnalysisSchema(Schema):
    model_config = ConfigDict(extra='forbid', strict=True)


class DraftAction(AnalysisSchema):
    # Extractive wording makes unsupported instructions detectable before storage.
    text: Text
    responsible: Text
    deadline_original: Text
    requires_review: bool
    source_utterance_ids: Sources


class DraftTopic(AnalysisSchema):
    title: Text
    summary: Text
    source_utterance_ids: Sources
    requires_review: bool
    action_items: list[DraftAction] = Field(max_length=100)


class DraftKeyPoint(AnalysisSchema):
    direction: Text
    metric: Text
    problem: Text
    source_utterance_ids: Sources
    requires_review: bool


class AnalysisDraft(AnalysisSchema):
    summary: str = Field(max_length=16000)
    key_points: list[DraftKeyPoint] = Field(max_length=100)
    topics: list[DraftTopic] = Field(max_length=100)


class Analyzer(Protocol):
    def analyze(self, utterances: list[Utterance], speakers: list[Speaker]) -> AnalysisDraft: ...


def validate_evidence(draft: AnalysisDraft, utterances: list[Utterance]) -> AnalysisDraft:
    """Reject unknown/cross-meeting IDs and unsupported extractive facts.

    This checks evidence existence, not semantic truth. All generated content remains
    a draft requiring human review, even when its phrases occur in the transcript.
    """
    draft = AnalysisDraft.model_validate(draft.model_dump())
    by_id = {item.id: item for item in utterances}
    if not by_id:
        if draft.summary.strip() or draft.key_points or draft.topics:
            raise LocalError('ollama_invalid_response')
        return draft

    def sources(ids):
        if len(set(ids)) != len(ids) or any(uid not in by_id for uid in ids):
            raise LocalError('ollama_invalid_response')
        return [by_id[uid].text for uid in ids]

    def supported(text, texts):
        return any(text in source for source in texts)

    if not draft.summary.strip():
        raise LocalError('ollama_invalid_response')
    for point in draft.key_points:
        texts = sources(point.source_utterance_ids)
        for value in (point.metric, point.problem):
            if value != 'не указан' and not supported(value, texts):
                raise LocalError('ollama_invalid_response')
        point.requires_review = True
    for topic in draft.topics:
        sources(topic.source_utterance_ids)
        topic.requires_review = True
        for action in topic.action_items:
            texts = sources(action.source_utterance_ids)
            if not set(action.source_utterance_ids) <= set(topic.source_utterance_ids):
                raise LocalError('ollama_invalid_response')
            if action.text != 'не указан' and not supported(action.text, texts):
                raise LocalError('ollama_invalid_response')
            for field in ('responsible', 'deadline_original'):
                value = getattr(action, field)
                # Never equate a diarization label with an assignee.
                if value != 'не указан' and (not supported(value, texts) or value.startswith(('Спикер ', 'speaker_'))):
                    setattr(action, field, 'не указан')
            action.requires_review = True
    return draft
