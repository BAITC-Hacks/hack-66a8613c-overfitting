import sqlite3
from threading import Lock
from uuid import uuid4

from .errors import LocalError
from .repository import MeetingRepository
from .schemas import Meeting
from .sources.storage import MeetingStorage, confined
from .processing.audio import AudioPreparer
from .database import connect


class MeetingService:
    def __init__(self, storage: MeetingStorage, repository: MeetingRepository, preparer: AudioPreparer):
        self.storage = storage
        self.repository = repository
        self.preparer = preparer
        self.worker_lock = Lock()

    async def upload(self, file, title, meeting_date, timezone, limit):
        from pydantic import ValidationError
        try:
            meeting = Meeting(id=str(uuid4()), title=title.strip(), meeting_date=meeting_date or None,
                              timezone=timezone or None, machine_prepared=False)
        except (ValidationError, AttributeError):
            raise LocalError('invalid_metadata', 422) from None
        path, size, original_name = await self.storage.save(meeting.id, file, limit)
        try:
            return self.repository.create(meeting, path, size, original_name)
        except sqlite3.Error:
            try:
                self.storage.remove(meeting.id)
            except (LocalError, OSError):
                pass
            raise LocalError('save_failed', 500) from None

    def prepare(self, meeting_id: str):
        # FastAPI BackgroundTasks runs this sync function in its thread pool.
        # Waiting jobs remain queued; only one FFmpeg process is active.
        with self.worker_lock:
            try:
                if self.repository.status(meeting_id).status != 'queued':
                    return
                self.repository.update(meeting_id, 'preparing_audio')
                source = confined(self.storage.root, self.repository.source(meeting_id))
                if source.parent != self.storage.directory(meeting_id):
                    raise LocalError('unsafe_path', 409)
                destination = self.storage.file(meeting_id, 'audio.wav')
                result = self.preparer.prepare(source, destination)
                self.repository.update(meeting_id, 'ready_for_models', audio_path=result.path, duration=result.duration_seconds)
            except Exception as exc:
                code = exc.code if isinstance(exc, LocalError) else 'processing_failed'
                # Never log subprocess errors, file names, or user metadata.
                self.repository.update(meeting_id, 'failed', error=code)
                try:
                    self.storage.file(meeting_id, 'audio.wav').unlink(missing_ok=True)
                except (OSError, LocalError):
                    pass

    def delete(self, meeting_id: str):
        # Serialize the status check and row deletion with status transitions.
        with connect(self.repository.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM processing_jobs WHERE meeting_id=?', (meeting_id,)).fetchone()
            if row is None:
                raise LocalError('not_found', 404)
            if row['status'] in ('queued', 'preparing_audio'):
                raise LocalError('busy', 409)
            try:
                # Derive from UUID, NEVER from paths stored in the database.
                self.storage.remove(meeting_id)
            except OSError:
                raise LocalError('delete_failed', 500) from None
            db.execute('DELETE FROM meetings WHERE id=?', (meeting_id,))
