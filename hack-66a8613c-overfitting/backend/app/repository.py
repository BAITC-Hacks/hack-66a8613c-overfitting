from pathlib import Path
from uuid import uuid4

from .database import connect
from .errors import LocalError, MESSAGES
from .schemas import Meeting, ProcessingJob

STATUS_MESSAGES = {
    'queued': 'Запись сохранена. Ожидание подготовки аудио.',
    'preparing_audio': 'Подготовка аудио…',
    'ready_for_models': 'Аудио подготовлено. Локальные модели распознавания ещё не настроены.',
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
        return ProcessingJob(id=row['id'], meeting_id=meeting_id, status=row['status'], stage=row['stage'],
                             error_code=row['error_code'], message=STATUS_MESSAGES.get(row['status'], MESSAGES.get(row['error_code'], MESSAGES['processing_failed'])))

    def update(self, meeting_id: str, status: str, error: str | None = None, audio_path: Path | None = None, duration: float | None = None):
        with connect(self.database) as db:
            db.execute('UPDATE processing_jobs SET status=?,error_code=?,audio_path=? WHERE meeting_id=?',
                       (status, error, str(audio_path) if audio_path else None, meeting_id))
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
            db.execute("UPDATE processing_jobs SET status='failed',error_code='interrupted' WHERE status IN ('queued','preparing_audio')")
