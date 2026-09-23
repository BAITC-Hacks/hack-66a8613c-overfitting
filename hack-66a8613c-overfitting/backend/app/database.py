"""SQLite persistence models defined in schema.sql, using the standard library."""
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('PRAGMA foreign_keys = ON')
        existing = connection.execute("SELECT sql FROM sqlite_master WHERE name='processing_jobs'").fetchone()
        if existing and 'ready_for_models' not in existing[0]:
            # Preserve skeleton rows, without claiming old results are prepared audio.
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('DROP INDEX IF EXISTS one_processing_job')
            connection.execute('ALTER TABLE processing_jobs RENAME TO processing_jobs_legacy')
            connection.execute("""CREATE TABLE processing_jobs (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('queued','preparing_audio','ready_for_models','failed')),
                stage TEXT, error_code TEXT, audio_path TEXT)""")
            connection.execute("""INSERT INTO processing_jobs(id,meeting_id,status,stage,error_code)
                SELECT id, meeting_id, CASE WHEN status IN ('processing','ready') THEN 'failed' ELSE status END,
                stage, CASE WHEN status IN ('processing','ready') THEN 'legacy_state' ELSE error_code END
                FROM processing_jobs_legacy""")
            connection.execute('DROP TABLE processing_jobs_legacy')
            connection.commit()
        connection.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        existing = connection.execute("SELECT sql FROM sqlite_master WHERE name='processing_jobs'").fetchone()[0]
        if 'transcribing' not in existing:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('DROP INDEX IF EXISTS one_processing_job')
            connection.execute('ALTER TABLE processing_jobs RENAME TO processing_jobs_audio')
            connection.execute("""CREATE TABLE processing_jobs (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('queued','preparing_audio','ready_for_models','transcribing','diarizing','saving_transcript','ready','failed')),
                stage TEXT, error_code TEXT, audio_path TEXT, detected_language TEXT)""")
            connection.execute('INSERT INTO processing_jobs(id,meeting_id,status,stage,error_code,audio_path) SELECT id,meeting_id,status,stage,error_code,audio_path FROM processing_jobs_audio')
            connection.execute('DROP TABLE processing_jobs_audio')
            connection.execute("""CREATE UNIQUE INDEX one_processing_job ON processing_jobs((1))
                WHERE status IN ('preparing_audio','transcribing','diarizing','saving_transcript') OR (status='ready_for_models' AND stage='models')""")
            connection.commit()

        existing = connection.execute("SELECT sql FROM sqlite_master WHERE name='processing_jobs'").fetchone()[0]
        if 'analyzing' not in existing:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('DROP INDEX IF EXISTS one_processing_job')
            connection.execute('ALTER TABLE processing_jobs RENAME TO processing_jobs_transcription')
            connection.execute("""CREATE TABLE processing_jobs (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('queued','preparing_audio','ready_for_models','transcribing','diarizing','saving_transcript','analyzing','ready','failed')),
                stage TEXT, error_code TEXT, audio_path TEXT, detected_language TEXT)""")
            connection.execute('INSERT INTO processing_jobs SELECT * FROM processing_jobs_transcription')
            connection.execute('DROP TABLE processing_jobs_transcription')
            connection.execute("""CREATE UNIQUE INDEX one_processing_job ON processing_jobs((1))
                WHERE status IN ('preparing_audio','transcribing','diarizing','saving_transcript','analyzing') OR (status='ready_for_models' AND stage='models')""")
            connection.commit()


@contextmanager
def connect(path: Path):
    with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        yield connection
