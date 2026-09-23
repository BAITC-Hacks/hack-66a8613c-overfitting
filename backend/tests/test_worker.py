import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from backend.tests.helpers import temporary_directory
from threading import Event
import unittest
from unittest.mock import patch

from fastapi import UploadFile

from backend.app.database import initialize_database
from backend.app.errors import LocalError
from backend.app.processing.audio import AudioPreparer, PreparedAudio
from backend.app.repository import MeetingRepository
from backend.app.service import MeetingService
from backend.app.sources.storage import MeetingStorage
from backend.tests.test_audio import write_wav


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_directory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        database = root / 'meetings.sqlite3'
        initialize_database(database)
        self.service = MeetingService(MeetingStorage(root), MeetingRepository(database), AudioPreparer('ffmpeg', 'ffprobe'))

    def upload(self):
        return asyncio.run(self.service.upload(UploadFile(BytesIO(b'test'), filename='x.wav'), 'Test', None, None, 100)).meeting_id

    def test_workers_are_serialized(self):
        first, second = self.upload(), self.upload()
        entered, release, second_started = Event(), Event(), Event()
        calls = []
        def prepare(source, target):
            calls.append(source.parent.name)
            if source.parent.name == first:
                entered.set()
                self.assertTrue(release.wait(5))
            write_wav(target)
            return PreparedAudio(target, 1)
        def run_second():
            second_started.set()
            self.service.prepare(second)
        with patch.object(self.service.preparer, 'prepare', side_effect=prepare), ThreadPoolExecutor(2) as pool:
            one = pool.submit(self.service.prepare, first)
            try:
                self.assertTrue(entered.wait(5))
                two = pool.submit(run_second)
                self.assertTrue(second_started.wait(5))
                self.assertEqual(self.service.repository.status(second).status, 'queued')
                self.assertEqual(calls, [first])
            finally:
                release.set()
            one.result(5)
            two.result(5)
        self.assertEqual(calls, [first, second])
        self.assertEqual(self.service.repository.status(second).status, 'ready_for_models')

    def test_failed_preparation_cleans_output(self):
        for error in [LocalError('conversion_failed'), RuntimeError('PRIVATE DATA')]:
            meeting_id = self.upload()
            def fail(source, target):
                target.write_bytes(b'partial')
                raise error
            with patch.object(self.service.preparer, 'prepare', side_effect=fail):
                self.service.prepare(meeting_id)
            status = self.service.repository.status(meeting_id)
            self.assertEqual(status.status, 'failed')
            self.assertNotIn('PRIVATE', status.message)
            self.assertFalse(self.service.storage.file(meeting_id, 'audio.wav').exists())
