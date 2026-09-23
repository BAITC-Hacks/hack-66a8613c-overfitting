"""Assign by total temporal intersection, without inventing speakers or words."""
from uuid import uuid4

from .contracts import AsrSegment, DiarizationTurn
from ..schemas import Speaker, Utterance


def merge_transcript(meeting_id: str, segments: list[AsrSegment], turns: list[DiarizationTurn]):
    labels = list(dict.fromkeys(turn.speaker for turn in sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))))
    speakers = [Speaker(id=str(uuid4()), meeting_id=meeting_id, label=f'speaker_{i}', name=f'Спикер {i}')
                for i, _ in enumerate(labels, 1)]
    # Union same-speaker intervals so overlapping duplicate turns cannot inflate a score.
    intervals = {}
    for label in labels:
        merged = []
        for turn in sorted((turn for turn in turns if turn.speaker == label), key=lambda turn: turn.start):
            if merged and turn.start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], turn.end))
            else:
                merged.append((turn.start, turn.end))
        intervals[label] = merged
    utterances = []
    for segment in sorted(segments, key=lambda segment: (segment.start, segment.end)):
        scores = [sum(max(0.0, min(segment.end, end) - max(segment.start, start))
                      for start, end in intervals[label]) for label in labels]
        best = max(range(len(scores)), key=scores.__getitem__) if scores and max(scores) > 0 else None
        uncertain = best is None or sum(score > 0 for score in scores) > 1
        if best is not None:
            uncertain |= scores[best] < segment.end - segment.start - 0.001
        utterances.append(Utterance(id=str(uuid4()), meeting_id=meeting_id,
                                    speaker_id=speakers[best].id if best is not None else None,
                                    start_seconds=segment.start, end_seconds=segment.end,
                                    text=segment.text, requires_review=uncertain))
    return speakers, utterances
