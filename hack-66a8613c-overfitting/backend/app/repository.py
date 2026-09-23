from pathlib import Path
from uuid import uuid4

from .database import connect
from .errors import LocalError, MESSAGES
from .schemas import Meeting, PreparationResult, ProcessingJob, Speaker, Utterance

ACTIVE_STATUSES = ('queued', 'preparing_audio', 'transcribing', 'diarizing', 'saving_transcript')


def is_active(status: str, stage: str | None) -> bool:
    return status in ACTIVE_STATUSES or (status == 'ready_for_models' and stage == 'models')

STATUS_MESSAGES = {
    'queued': 'Запись сохранена. Ожидание подготовки аудио.',
    'preparing_audio': 'Подготовка аудио…',
    'ready_for_models': 'Аудио подготовлено. Подключение локальных моделей…',
    'transcribing': 'Локальное распознавание речи…',
    'diarizing': 'Определение говорящих…',
    'saving_transcript': 'Сопоставление реплик и сохранение транскрипта…',
    'ready': 'Транскрипт с метками говорящих готов. Требуется проверка человеком.',
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
            row = db.execute('SELECT id,title,meeting_date,timezone,machine_prepared FROM meetings WHERE id=?', (meeting_id,)).fetchone()
        if row is None:
            raise LocalError('not_found', 404)
        return Meeting(**dict(row))

    def source(self, meeting_id: str):
        with connect(self.database) as db:
            row = db.execute('SELECT storage_path FROM source_files WHERE meeting_id=?', (meeting_id,)).fetchone()
        if row is None:
            raise LocalError('not_found', 404)
        return Path(row['storage_path'])

    def recover_interrupted(self):
        with connect(self.database) as db:
            db.execute("UPDATE processing_jobs SET status='failed',error_code='interrupted' WHERE status IN ('queued','preparing_audio','transcribing','diarizing','saving_transcript') OR (status='ready_for_models' AND stage='models')")

    def save_transcript(self, meeting_id: str, speakers: list[Speaker], utterances: list[Utterance], language: str | None):
        # Publish data and final status together. No partial transcripts on ML/SQL failure.
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
            db.execute("UPDATE processing_jobs SET status='ready',stage='complete',error_code=NULL,detected_language=? WHERE meeting_id=?", (language, meeting_id))
            db.execute('UPDATE meetings SET machine_prepared=1 WHERE id=?', (meeting_id,))

    def result(self, meeting_id: str):
        job = self.status(meeting_id)
        with connect(self.database) as db:
            # Only published, complete data. The existing meeting payload remains intact.
            speakers = [Speaker(**dict(row)) for row in db.execute('SELECT * FROM speakers WHERE meeting_id=? ORDER BY rowid', (meeting_id,))] if job.status == 'ready' else []
            utterances = [Utterance(**dict(row)) for row in db.execute('SELECT * FROM utterances WHERE meeting_id=? ORDER BY start_seconds,end_seconds,rowid', (meeting_id,))] if job.status == 'ready' else []
        return PreparationResult(meeting=self.meeting(meeting_id), job=job, models_connected=job.status == 'ready',
                                 detected_language=job.detected_language, speakers=speakers, utterances=utterances)
