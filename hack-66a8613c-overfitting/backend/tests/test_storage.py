import asyncio
from io import BytesIO
from pathlib import Path
import sqlite3
from backend.tests.helpers import temporary_directory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import UploadFile

from backend.app.database import connect, initialize_database
from backend.app.errors import LocalError
from backend.app.repository import MeetingRepository
from backend.app.schemas import Meeting
from backend.app.sources.storage import MeetingStorage, confined


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = MeetingStorage(self.root / 'data')
        self.database = self.storage.root / 'meetings.sqlite3'
        initialize_database(self.database)
        self.repo = MeetingRepository(self.database)
        self.id = str(uuid4())

    def save(self, name='sample.wav', body=b'unit-test-bytes', limit=200*1024*1024):
        upload = UploadFile(BytesIO(body), filename=name)
        return asyncio.run(self.storage.save(self.id, upload, limit))

    def test_allowed_files_and_metadata(self):
        for extension in ['mp4', 'm4a', 'mp3', 'wav', 'webm']:
            with self.subTest(extension=extension):
                self.id = str(uuid4())
                path, size, name = self.save('../../unsafe/' + 'sample.' + extension.upper())
                self.assertEqual(path, self.storage.root / self.id / ('source.' + extension))
                self.assertEqual(size, 15)
                self.assertNotIn('/', name)
                meeting = Meeting(id=self.id, title='Test', meeting_date='2026-09-23', timezone='Asia/Qyzylorda')
                job = self.repo.create(meeting, path, size, name)
                self.assertEqual(job.status, 'queued')
                self.assertEqual(self.repo.meeting(self.id).timezone, 'Asia/Qyzylorda')
                self.assertEqual(self.repo.source(self.id), path)

    def test_empty_unsupported_oversize_are_cleaned(self):
        for name, body, limit, code in [('x.exe', b'x', 10, 'unsupported_extension'), ('x.wav', b'', 10, 'empty_file'), ('x.wav', b'12345', 4, 'file_too_large')]:
            with self.subTest(code=code), self.assertRaises(LocalError) as error:
                self.save(name, body, limit)
            self.assertEqual(error.exception.code, code)
            self.assertFalse(self.storage.directory(self.id).exists())

    def test_disk_error_is_safe(self):
        with patch.object(Path, 'open', side_effect=OSError('PRIVATE PATH')), self.assertRaises(LocalError) as error:
            self.save()
        self.assertEqual(error.exception.code, 'save_failed')
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertFalse(self.storage.directory(self.id).exists())

    def test_path_confinement(self):
        with self.assertRaises(LocalError):
            self.storage.directory('../outside')
        with self.assertRaises(LocalError):
            self.storage.file(self.id, '../../outside')
        with self.assertRaises(LocalError):
            confined(self.storage.root, self.root / 'outside')

    def test_legacy_migration_preserves_rows(self):
        legacy = self.storage.root / 'legacy.sqlite3'
        with connect(legacy) as db:
            db.execute('CREATE TABLE meetings(id TEXT PRIMARY KEY, title TEXT)')
            db.execute("INSERT INTO meetings VALUES ('m','preserved')")
            db.execute("CREATE TABLE processing_jobs(id TEXT PRIMARY KEY,meeting_id TEXT,status TEXT CHECK(status IN ('queued','processing','ready','failed')),stage TEXT,error_code TEXT)")
            db.execute("INSERT INTO processing_jobs VALUES ('j','m','ready',NULL,NULL)")
        initialize_database(legacy)
        initialize_database(legacy)
        with connect(legacy) as db:
            row = db.execute('SELECT * FROM processing_jobs').fetchone()
            self.assertEqual(row['id'], 'j')
            self.assertEqual(row['status'], 'failed')
            self.assertEqual(row['error_code'], 'legacy_state')

    def test_database_one_preparation_and_restart_recovery(self):
        for _ in range(2):
            self.id = str(uuid4())
            path, size, name = self.save()
            self.repo.create(Meeting(id=self.id, title='Test'), path, size, name)
        with connect(self.database) as db:
            ids = [row[0] for row in db.execute('SELECT meeting_id FROM processing_jobs')]
        self.repo.update(ids[0], 'preparing_audio')
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.update(ids[1], 'preparing_audio')
        self.repo.recover_interrupted()
        for meeting_id in ids:
            self.assertEqual(self.repo.status(meeting_id).error_code, 'interrupted')
