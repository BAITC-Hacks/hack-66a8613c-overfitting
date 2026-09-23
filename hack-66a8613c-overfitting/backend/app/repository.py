from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import json
import re

from .database import connect
from .errors import LocalError, MESSAGES
from .schemas import Meeting, PreparationResult, ProcessingJob, Speaker, Utterance, Topic, ActionItem, KeyPoint, MeetingEdits
from .processing.analysis import AnalysisDraft, validate_evidence

ACTIVE_STATUSES = ('queued', 'preparing_audio', 'transcribing', 'diarizing', 'saving_transcript', 'analyzing')


def is_active(status: str, stage: str | None) -> bool:
    return status in ACTIVE_STATUSES or (status == 'ready_for_models' and stage == 'models')

STATUS_MESSAGES = {
    'queued': 'Запись сохранена. Ожидание подготовки аудио.',
    'preparing_audio': 'Подготовка аудио…',
    'ready_for_models': 'Аудио подготовлено. Подключение локальных моделей…',
    'transcribing': 'Локальное распознавание речи…',
    'diarizing': 'Определение говорящих…',
    'saving_transcript': 'Сопоставление реплик и сохранение транскрипта…',
    'analyzing': 'Локальный анализ транскрипта: саммари, проблемы и поручения…',
    'ready': 'Транскрипт и черновик протокола готовы. Требуется проверка человеком.',
}


class MeetingRepository:
    def __init__(self, database: Path):
        self.database = database

    def create(self, meeting: Meeting, path: Path, size: int, original_name: str):
        with connect(self.database) as db:
            db.execute('INSERT INTO meetings(id,title,meeting_date,timezone,machine_prepared) VALUES (?,?,?,?,0)',
                       (meeting.id, meeting.title, meeting.meeting_date.isoformat() if meeting.meeting_date else None, meeting.timezone))
            db.execute('INSERT INTO source_files VALUES (?,?,?,?,?,NULL)',
                       (str(uuid4()), meeting.id, original_name, str(path), size))
            db.execute("INSERT INTO processing_jobs(id,meeting_id,status,stage) VALUES (?,?,'queued','audio')",
                       (str(uuid4()), meeting.id))
        return self.status(meeting.id)

    def status(self, meeting_id: str):
        with connect(self.database) as db:
            row = db.execute('SELECT * FROM processing_jobs WHERE meeting_id=?', (meeting_id,)).fetchone()
        if row is None:
            raise LocalError('not_found', 404)
        message = STATUS_MESSAGES.get(row['status'], MESSAGES.get(row['error_code'], MESSAGES['processing_failed']))
        if row['status'] == 'ready_for_models' and row['stage'] != 'models':
            message = 'Аудио подготовлено ранее. Для распознавания загрузите запись повторно.'
        if row['status'] == 'ready' and row['stage'] == 'complete':
            message = 'Транскрипт подготовлен ранее без анализа. Для анализа загрузите запись повторно.'
        return ProcessingJob(id=row['id'], meeting_id=meeting_id, status=row['status'], stage=row['stage'],
                             detected_language=row['detected_language'], error_code=row['error_code'], message=message)

    def update(self, meeting_id: str, status: str, error: str | None = None, audio_path: Path | None = None, duration: float | None = None, stage: str | None = None):
        with connect(self.database) as db:
            db.execute('UPDATE processing_jobs SET status=?,error_code=?,audio_path=COALESCE(?,audio_path),stage=COALESCE(?,stage) WHERE meeting_id=?',
                       (status, error, str(audio_path) if audio_path else None, stage, meeting_id))
            if duration is not None:
                db.execute('UPDATE source_files SET duration_seconds=? WHERE meeting_id=?', (duration, meeting_id))

    def meeting(self, meeting_id: str):
        with connect(self.database) as db:
            row = db.execute('SELECT id,title,meeting_date,timezone,summary,machine_prepared,approved_at,participants_json FROM meetings WHERE id=?', (meeting_id,)).fetchone()
        if row is None:
            raise LocalError('not_found', 404)
        values = dict(row)
        values['participants'] = json.loads(values.pop('participants_json'))
        return Meeting(**values)

    def edit(self, meeting_id: str, edits: MeetingEdits):
        with connect(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            meeting = db.execute('SELECT id FROM meetings WHERE id=?', (meeting_id,)).fetchone()
            if meeting is None:
                raise LocalError('not_found', 404)
            job = db.execute('SELECT status FROM processing_jobs WHERE meeting_id=?', (meeting_id,)).fetchone()
            analyzed = db.execute('SELECT 1 FROM analyses WHERE meeting_id=?', (meeting_id,)).fetchone()
            if job is None or job['status'] != 'ready' or analyzed is None:
                raise LocalError('result_not_ready', 409)
            for speaker in edits.speakers:
                updated = db.execute('UPDATE speakers SET name=? WHERE meeting_id=? AND id=?',
                                     (speaker.name, meeting_id, speaker.id))
                if updated.rowcount != 1:
                    raise LocalError('entity_not_found', 404)
            for action in edits.action_items:
                exists = db.execute('SELECT 1 FROM action_items WHERE meeting_id=? AND id=?', (meeting_id, action.id)).fetchone()
                if exists is None:
                    raise LocalError('entity_not_found', 404)
                # SQL column names are constants; no IDs/fields from the request are interpolated.
                for field in ('text', 'responsible', 'deadline_original', 'requires_review'):
                    if field in action.model_fields_set:
                        db.execute(f'UPDATE action_items SET {field}=? WHERE meeting_id=? AND id=?',
                                   (getattr(action, field), meeting_id, action.id))
                if 'deadline_original' in action.model_fields_set:
                    db.execute('UPDATE action_items SET deadline_date=NULL WHERE meeting_id=? AND id=?', (meeting_id, action.id))
            if edits.speakers:
                names = [row['name'] for row in db.execute('SELECT name FROM speakers WHERE meeting_id=? ORDER BY rowid', (meeting_id,))
                         if row['name'] and not re.fullmatch(r'Спикер \d+', row['name'])]
                db.execute('UPDATE meetings SET participants_json=? WHERE id=?', (json.dumps(list(dict.fromkeys(names)), ensure_ascii=False), meeting_id))
            if edits.speakers or edits.action_items:
                db.execute('UPDATE meetings SET approved_at=NULL WHERE id=?', (meeting_id,))
                # Artifacts are never served by path; existing snapshots become inaccessible.
                db.execute('DELETE FROM exports WHERE meeting_id=?', (meeting_id,))
            if edits.approve:
                pending = db.execute('SELECT 1 FROM action_items WHERE meeting_id=? AND requires_review=1 LIMIT 1', (meeting_id,)).fetchone()
                if pending is not None:
                    raise LocalError('review_required', 409)
                db.execute('UPDATE meetings SET approved_at=COALESCE(approved_at,?) WHERE id=?',
                           (datetime.now(timezone.utc).isoformat(), meeting_id))

    def record_export(self, meeting_id: str, format: str, path: Path):
        with connect(self.database) as db:
            db.execute('INSERT INTO exports VALUES (?,?,?,?,?)',
                       (str(uuid4()), meeting_id, format, str(path), datetime.now(timezone.utc).isoformat()))

    def source(self, meeting_id: str):
        with connect(self.database) as db:
            row = db.execute('SELECT storage_path FROM source_files WHERE meeting_id=?', (meeting_id,)).fetchone()
        if row is None:
            raise LocalError('not_found', 404)
        return Path(row['storage_path'])

    def recover_interrupted(self):
        with connect(self.database) as db:
            db.execute("UPDATE processing_jobs SET status='failed',error_code='interrupted' WHERE status IN ('queued','preparing_audio','transcribing','diarizing','saving_transcript','analyzing') OR (status='ready_for_models' AND stage='models')")

    def save_transcript(self, meeting_id: str, speakers: list[Speaker], utterances: list[Utterance], language: str | None):
        # Publish the complete transcript and analyzing transition atomically.
        with connect(self.database) as db:
            for speaker in speakers:
                if speaker.meeting_id != meeting_id:
                    raise ValueError('Meeting mismatch')
                db.execute('INSERT INTO speakers(id,meeting_id,label,name) VALUES (?,?,?,?)',
                           (speaker.id, meeting_id, speaker.label, speaker.name))
            for utterance in utterances:
                if utterance.meeting_id != meeting_id:
                    raise ValueError('Meeting mismatch')
                db.execute('INSERT INTO utterances(id,meeting_id,speaker_id,start_seconds,end_seconds,text,requires_review) VALUES (?,?,?,?,?,?,?)',
                           (utterance.id, meeting_id, utterance.speaker_id, utterance.start_seconds,
                            utterance.end_seconds, utterance.text, utterance.requires_review))
            db.execute("UPDATE processing_jobs SET status='analyzing',stage='analysis',error_code=NULL,detected_language=? WHERE meeting_id=?", (language, meeting_id))
            db.execute('UPDATE meetings SET machine_prepared=1 WHERE id=?', (meeting_id,))

    def save_analysis(self, meeting_id: str, draft: AnalysisDraft):
        # Recheck against persisted inputs at the storage boundary, including fake adapters.
        with connect(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT status FROM processing_jobs WHERE meeting_id=?', (meeting_id,)).fetchone()
            if job is None or job['status'] != 'analyzing':
                raise LocalError('analysis_failed')
            utterances = [Utterance(**dict(row)) for row in db.execute('SELECT * FROM utterances WHERE meeting_id=?', (meeting_id,))]
            draft = validate_evidence(draft, utterances)
            by_id = {item.id: item for item in utterances}
            for position, point in enumerate(draft.key_points, 1):
                point_id = str(uuid4())
                db.execute('INSERT INTO key_points VALUES (?,?,?,?,?,?,?)',
                           (point_id, meeting_id, position, point.direction, point.metric, point.problem, point.requires_review))
                db.executemany('INSERT INTO key_point_sources VALUES (?,?,?)',
                               [(meeting_id, point_id, uid) for uid in point.source_utterance_ids])
            for position, topic in enumerate(draft.topics, 1):
                topic_id = str(uuid4())
                db.execute('INSERT INTO topics VALUES (?,?,?,?,?)', (topic_id, meeting_id, position, topic.title, topic.summary))
                db.executemany('INSERT INTO topic_sources VALUES (?,?,?)',
                               [(meeting_id, topic_id, uid) for uid in topic.source_utterance_ids])
                for action in topic.action_items:
                    action_id = str(uuid4())
                    first = min((by_id[uid] for uid in action.source_utterance_ids), key=lambda u: u.start_seconds)
                    db.execute('INSERT INTO action_items VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (action_id, meeting_id, topic_id, action.text, action.responsible, action.deadline_original,
                                None, first.id, first.start_seconds, action.requires_review))
                    db.executemany('INSERT INTO action_sources VALUES (?,?,?)',
                                   [(meeting_id, action_id, uid) for uid in action.source_utterance_ids])
            db.execute('INSERT INTO analyses VALUES (?,1)', (meeting_id,))
            db.execute('UPDATE meetings SET summary=? WHERE id=?', (draft.summary, meeting_id))
            db.execute("UPDATE processing_jobs SET status='ready',stage='analysis_complete',error_code=NULL WHERE meeting_id=?", (meeting_id,))

    def result(self, meeting_id: str):
        job = self.status(meeting_id)
        with connect(self.database) as db:
            # Transcript was committed atomically and remains available after analysis errors.
            speakers = [Speaker(**dict(row)) for row in db.execute('SELECT * FROM speakers WHERE meeting_id=? ORDER BY rowid', (meeting_id,))]
            utterances = [Utterance(**dict(row)) for row in db.execute('SELECT * FROM utterances WHERE meeting_id=? ORDER BY start_seconds,end_seconds,rowid', (meeting_id,))]
            completed = db.execute('SELECT 1 FROM analyses WHERE meeting_id=?', (meeting_id,)).fetchone() is not None
            def sources(table, key, item_id):
                # Identifiers here are internal constants, never LLM or request values.
                return [row[0] for row in db.execute(f'SELECT utterance_id FROM {table} WHERE meeting_id=? AND {key}=? ORDER BY rowid', (meeting_id, item_id))]
            topics = [Topic(**dict(row), source_utterance_ids=sources('topic_sources', 'topic_id', row['id']))
                      for row in db.execute('SELECT * FROM topics WHERE meeting_id=? ORDER BY position', (meeting_id,))] if completed else []
            actions = [ActionItem(**dict(row), source_utterance_ids=sources('action_sources', 'action_id', row['id']))
                       for row in db.execute('SELECT * FROM action_items WHERE meeting_id=? ORDER BY rowid', (meeting_id,))] if completed else []
            points = [KeyPoint(**dict(row), source_utterance_ids=sources('key_point_sources', 'key_point_id', row['id']))
                      for row in db.execute('SELECT * FROM key_points WHERE meeting_id=? ORDER BY position', (meeting_id,))] if completed else []
        meeting = self.meeting(meeting_id)
        if meeting.approved_at is not None:
            for item in [*topics, *points]:
                item.requires_review = False
        return PreparationResult(meeting=meeting, job=job, models_connected=job.status == 'ready',
                                 detected_language=job.detected_language, speakers=speakers, utterances=utterances,
                                 summary=meeting.summary if completed else '', analysis_completed=completed,
                                 topics=topics, action_items=actions, key_points=points,
                                 requires_review=meeting.approved_at is None)
