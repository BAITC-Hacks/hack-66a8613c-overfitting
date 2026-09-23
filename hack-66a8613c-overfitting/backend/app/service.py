import sqlite3
from threading import Lock, RLock
from uuid import uuid4

from .errors import LocalError
from .repository import MeetingRepository, is_active
from .schemas import Meeting
from .sources.storage import MeetingStorage, confined
from .processing.audio import AudioPreparer
from .database import connect
from . import config
from .processing.adapters import FasterWhisperAdapter, PyannoteAdapter
from .processing.contracts import Transcriber, Diarizer
from .processing.alignment import merge_transcript
from .processing.analysis import Analyzer
from .processing.ollama import OllamaAdapter


class MeetingService:
    def __init__(self, storage: MeetingStorage, repository: MeetingRepository, preparer: AudioPreparer,
                 transcriber: Transcriber | None = None, diarizer: Diarizer | None = None,
                 analyzer: Analyzer | None = None):
        self.storage = storage
        self.repository = repository
        self.preparer = preparer
        self.worker_lock = Lock()
        # This deployment uses one Uvicorn worker. Review/export/delete share a lock
        # so bytes are produced from one approved snapshot, never from mixed edits.
        self.review_lock = RLock()
        self.transcriber = transcriber if transcriber is not None else FasterWhisperAdapter(config.WHISPER_MODEL_PATH)
        self.diarizer = diarizer if diarizer is not None else PyannoteAdapter(config.PYANNOTE_MODEL_PATH)
        self.analyzer = analyzer if analyzer is not None else OllamaAdapter(config.OLLAMA_BASE_URL, config.OLLAMA_MODEL)

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
            audio_prepared = False
            failure_code = 'processing_failed'
            try:
                if self.repository.status(meeting_id).status != 'queued':
                    return
                self.repository.update(meeting_id, 'preparing_audio')
                source = confined(self.storage.root, self.repository.source(meeting_id))
                if source.parent != self.storage.directory(meeting_id):
                    raise LocalError('unsafe_path', 409)
                destination = self.storage.file(meeting_id, 'audio.wav')
                result = self.preparer.prepare(source, destination)
                audio_prepared = True
                self.repository.update(meeting_id, 'ready_for_models', audio_path=result.path, duration=result.duration_seconds, stage='models')
                failure_code = 'transcription_failed'
                self.repository.update(meeting_id, 'transcribing', stage='transcription')
                transcription = self.transcriber.transcribe(result.path)
                failure_code = 'diarization_failed'
                self.repository.update(meeting_id, 'diarizing', stage='diarization')
                turns = self.diarizer.diarize(result.path)
                failure_code = 'transcript_save_failed'
                self.repository.update(meeting_id, 'saving_transcript', stage='alignment')
                speakers, utterances = merge_transcript(meeting_id, transcription.segments, turns)
                self.repository.save_transcript(meeting_id, speakers, utterances, transcription.detected_language)
                failure_code = 'analysis_failed'
                saved = self.repository.result(meeting_id)
                analysis = self.analyzer.analyze(saved.utterances, saved.speakers)
                self.repository.save_analysis(meeting_id, analysis)
            except Exception as exc:
                code = exc.code if isinstance(exc, LocalError) else failure_code
                # Never log subprocess errors, file names, or user metadata.
                self.repository.update(meeting_id, 'failed', error=code)
                try:
                    if not audio_prepared:
                        self.storage.file(meeting_id, 'audio.wav').unlink(missing_ok=True)
                except (OSError, LocalError):
                    pass

    def delete(self, meeting_id: str):
        with self.review_lock:
            self._delete(meeting_id)

    def edit(self, meeting_id: str, edits):
        with self.review_lock:
            self.repository.edit(meeting_id, edits)
            return self.repository.result(meeting_id)

    def export(self, meeting_id: str, format: str):
        with self.review_lock:
            result = self.repository.result(meeting_id)  # 404 before approval check.
            if not result.meeting.approved_at or not result.analysis_completed or result.job.status != 'ready':
                raise LocalError('not_approved', 409)
            from .exporting.documents import ProtocolExporter
            path = None
            try:
                path = ProtocolExporter(self.storage, config.LIBREOFFICE_PATH).generate(result, format)
                path = confined(self.storage.root, path)
                if path.parent != self.storage.directory(meeting_id):
                    raise LocalError('unsafe_path', 409)
                with path.open('rb') as stream:
                    content = stream.read(32 * 1024 * 1024 + 1)
                if not content or len(content) > 32 * 1024 * 1024:
                    raise LocalError('export_failed', 500)
                self.repository.record_export(meeting_id, format, path)
                # Read under the lock: delayed FileResponse streaming could race deletion.
                return content, f'meeting-{result.meeting.id}.{format}'
            except Exception as exc:
                if path is not None:
                    try:
                        safe = self.storage.file(meeting_id, path.name)
                        if safe == path:
                            safe.unlink(missing_ok=True)
                    except (OSError, LocalError):
                        pass
                if isinstance(exc, LocalError):
                    raise
                raise LocalError('export_failed', 500) from None

    def _delete(self, meeting_id: str):
        # Serialize the status check and row deletion with status transitions.
        with connect(self.repository.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,stage FROM processing_jobs WHERE meeting_id=?', (meeting_id,)).fetchone()
            if row is None:
                raise LocalError('not_found', 404)
            if is_active(row['status'], row['stage']):
                raise LocalError('busy', 409)
            try:
                # Derive from UUID, NEVER from paths stored in the database.
                self.storage.remove(meeting_id)
            except OSError:
                raise LocalError('delete_failed', 500) from None
            db.execute('DELETE FROM meetings WHERE id=?', (meeting_id,))
