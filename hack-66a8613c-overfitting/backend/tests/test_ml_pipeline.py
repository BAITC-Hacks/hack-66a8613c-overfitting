import asyncio
from io import BytesIO
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch

from fastapi import UploadFile
from fastapi.testclient import TestClient

from backend.app import config
from backend.app.database import connect, initialize_database
from backend.app.errors import LocalError
from backend.app.main import app
from backend.app.processing.audio import PreparedAudio
from backend.app.processing.contracts import AsrSegment, DiarizationTurn, Transcription
from backend.app.processing.adapters import FasterWhisperAdapter
from backend.app.repository import MeetingRepository
from backend.app.schemas import Speaker, Utterance
from backend.app.service import MeetingService
from backend.app.sources.storage import MeetingStorage
from backend.tests.helpers import temporary_directory


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_directory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.database = self.root / 'meetings.sqlite3'
        initialize_database(self.database)
        self.repository = MeetingRepository(self.database)
        self.transcriber = Mock(transcribe=Mock(return_value=Transcription(
            segments=[AsrSegment(start=0, end=1, text='unit fixture'), AsrSegment(start=1, end=2, text='second fixture')], detected_language='kk')))
        self.diarizer = Mock(diarize=Mock(return_value=[DiarizationTurn(start=0, end=1, speaker='A'), DiarizationTurn(start=1, end=2, speaker='B')]))
        self.preparer = Mock(prepare=Mock(side_effect=self.prepare_audio))
        self.service = MeetingService(MeetingStorage(self.root), self.repository, self.preparer, self.transcriber, self.diarizer)

    def prepare_audio(self, source, target):
        target.write_bytes(b'not real audio; adapters are mocked')
        return PreparedAudio(target, 2)

    def upload(self):
        return asyncio.run(self.service.upload(UploadFile(BytesIO(b'fixture'), filename='x.wav'), 'Test', None, None, 100)).meeting_id

    def test_pipeline_statuses_and_persistence(self):
        meeting_id = self.upload()
        statuses = ['queued']
        update = self.repository.update
        def capture(mid, status, *args, **kwargs):
            statuses.append(status)
            update(mid, status, *args, **kwargs)
        with patch.object(self.repository, 'update', side_effect=capture):
            self.service.prepare(meeting_id)
        result = self.repository.result(meeting_id)
        self.assertEqual(statuses, ['queued', 'preparing_audio', 'ready_for_models', 'transcribing', 'diarizing', 'saving_transcript'])
        self.assertEqual(result.job.status, 'ready')
        self.assertEqual(result.detected_language, 'kk')
        self.assertEqual([speaker.name for speaker in result.speakers], ['Спикер 1', 'Спикер 2'])
        self.assertEqual([u.start_seconds for u in result.utterances], [0, 1])
        self.assertEqual(result.utterances[0].speaker_id, result.speakers[0].id)
        with connect(self.database) as db:
            self.assertTrue(Path(db.execute('SELECT audio_path FROM processing_jobs').fetchone()[0]).is_file())
        self.service.delete(meeting_id)
        with connect(self.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM utterances').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM speakers').fetchone()[0], 0)

    def test_missing_model_preserves_wav_without_output(self):
        self.service.transcriber = FasterWhisperAdapter(self.root / 'missing-model')
        meeting_id = self.upload()
        self.service.prepare(meeting_id)
        result = self.repository.result(meeting_id)
        self.assertEqual(result.job.error_code, 'whisper_model_missing')
        self.assertEqual(result.job.status, 'failed')
        self.assertFalse(result.models_connected)
        self.assertEqual(result.utterances, [])
        self.assertTrue((self.root / meeting_id / 'audio.wav').exists())
        self.diarizer.diarize.assert_not_called()

    def test_adapter_errors_do_not_publish_partial_transcript(self):
        for component, error, code in [
            ('transcriber', LocalError('cuda_unavailable'), 'cuda_unavailable'),
            ('transcriber', RuntimeError('PRIVATE'), 'transcription_failed'),
            ('diarizer', LocalError('pyannote_model_missing'), 'pyannote_model_missing'),
            ('diarizer', RuntimeError('PRIVATE'), 'diarization_failed')]:
            with self.subTest(code=code):
                adapter = getattr(self.service, component)
                method = 'transcribe' if component == 'transcriber' else 'diarize'
                meeting_id = self.upload()
                with patch.object(adapter, method, side_effect=error):
                    self.service.prepare(meeting_id)
                result = self.repository.result(meeting_id)
                self.assertEqual(result.job.error_code, code)
                self.assertNotIn('PRIVATE', result.job.message)
                self.assertEqual(result.utterances, [])
                self.assertTrue((self.root / meeting_id / 'audio.wav').is_file())

    def test_deletion_is_blocked_through_all_ml_stages(self):
        meeting_id = self.upload()
        for status in ['ready_for_models', 'transcribing', 'diarizing', 'saving_transcript']:
            self.repository.update(meeting_id, status, stage='models')
            with self.assertRaises(LocalError) as error:
                self.service.delete(meeting_id)
            self.assertEqual(error.exception.code, 'busy')
        self.repository.recover_interrupted()
        self.assertEqual(self.repository.status(meeting_id).error_code, 'interrupted')

    def test_atomic_save_rolls_back_speakers_and_utterances(self):
        meeting_id = self.upload()
        speaker = Speaker(id='speaker', meeting_id=meeting_id, label='speaker_1', name='Спикер 1')
        invalid = Utterance(id='u', meeting_id=meeting_id, speaker_id='absent', start_seconds=0, end_seconds=1, text='fixture')
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.save_transcript(meeting_id, [speaker], [invalid], 'ru')
        with connect(self.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM speakers').fetchone()[0], 0)
        self.assertEqual(self.repository.status(meeting_id).status, 'queued')

    def test_result_api_returns_persisted_segments(self):
        with patch.multiple(config, DATA_DIR=self.root, DATABASE_PATH=self.database), TestClient(app) as client:
            app.state.service = self.service
            response = client.post('/api/meetings', data={'title': 'Test'}, files={'file': ('x.wav', b'fixture')})
            self.assertEqual(response.status_code, 202)
            meeting_id = response.json()['meeting_id']
            result = client.get(f'/api/meetings/{meeting_id}/result').json()
            self.assertEqual(result['job']['status'], 'ready')
            self.assertEqual(result['detected_language'], 'kk')
            self.assertEqual(len(result['utterances']), 2)
            self.assertEqual(result['utterances'][0]['text'], 'unit fixture')

    def test_audio_schema_migration_preserves_finished_wav(self):
        old = self.root / 'old.sqlite3'
        with connect(old) as db:
            db.execute('CREATE TABLE meetings(id TEXT PRIMARY KEY, title TEXT)')
            db.execute("INSERT INTO meetings VALUES ('m','Test')")
            db.execute("CREATE TABLE processing_jobs(id TEXT PRIMARY KEY,meeting_id TEXT,status TEXT CHECK(status IN ('queued','preparing_audio','ready_for_models','failed')),stage TEXT,error_code TEXT,audio_path TEXT)")
            db.execute("INSERT INTO processing_jobs VALUES ('j','m','ready_for_models','audio',NULL,'preserved.wav')")
        initialize_database(old)
        initialize_database(old)
        with connect(old) as db:
            row = db.execute('SELECT * FROM processing_jobs').fetchone()
            self.assertEqual(row['status'], 'ready_for_models')
            self.assertEqual(row['audio_path'], 'preserved.wav')
            self.assertIsNone(row['detected_language'])
