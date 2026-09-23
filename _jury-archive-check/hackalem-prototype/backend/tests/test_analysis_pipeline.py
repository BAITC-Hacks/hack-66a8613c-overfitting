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
from backend.app.processing.analysis import AnalysisDraft
from backend.app.processing.audio import PreparedAudio
from backend.app.processing.contracts import AsrSegment, DiarizationTurn, Transcription
from backend.app.repository import MeetingRepository
from backend.app.service import MeetingService
from backend.app.sources.storage import MeetingStorage
from backend.tests.helpers import temporary_directory
from backend.tests.test_ollama import draft_data, transcript


class AnalysisPipelineTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_directory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.database = self.root / 'test.sqlite3'
        initialize_database(self.database)
        self.repository = MeetingRepository(self.database)
        self.analyzer = Mock(analyze=Mock(side_effect=self.analyze))
        self.service = MeetingService(MeetingStorage(self.root), self.repository,
            Mock(prepare=Mock(side_effect=self.prepare)),
            Mock(transcribe=Mock(return_value=Transcription(segments=[AsrSegment(start=1, end=3, text=transcript()[0].text)], detected_language='ru'))),
            Mock(diarize=Mock(return_value=[DiarizationTurn(start=1, end=3, speaker='A')])), self.analyzer)

    def prepare(self, source, target):
        target.write_bytes(b'unit fixture')
        return PreparedAudio(target, 3)

    def analyze(self, utterances, speakers):
        self.assertEqual(self.repository.status(utterances[0].meeting_id).status, 'analyzing')
        self.assertEqual(self.repository.result(utterances[0].meeting_id).utterances, utterances)
        data = draft_data()
        for item in [data['key_points'][0], data['topics'][0], data['topics'][0]['action_items'][0]]:
            item['source_utterance_ids'] = [utterances[0].id]
        return AnalysisDraft.model_validate(data)

    def upload(self):
        return asyncio.run(self.service.upload(UploadFile(BytesIO(b'fixture'), filename='test.wav'), 'Test', None, None, 100)).meeting_id

    def test_persistence_sources_api_and_cascade(self):
        with patch.multiple(config, DATA_DIR=self.root, DATABASE_PATH=self.database), TestClient(app) as client:
            app.state.service = self.service
            response = client.post('/api/meetings', data={'title': 'Test'}, files={'file': ('test.wav', b'fixture')})
            self.assertEqual(response.status_code, 202)
            mid = response.json()['meeting_id']
            result = client.get(f'/api/meetings/{mid}/result').json()
            self.assertEqual(result['job']['status'], 'ready')
            self.assertTrue(result['analysis_completed'])
            self.assertEqual(result['summary'], draft_data()['summary'])
            self.assertEqual(result['meeting']['summary'], result['summary'])
            uid = result['utterances'][0]['id']
            for item in [result['topics'][0], result['action_items'][0], result['key_points'][0]]:
                self.assertEqual(item['source_utterance_ids'], [uid])
                self.assertTrue(item['requires_review'])
            self.assertEqual(result['action_items'][0]['utterance_id'], uid)
            self.assertEqual(result['action_items'][0]['timestamp_seconds'], 1)
            self.assertIsNone(result['action_items'][0]['deadline_date'])
            initialize_database(self.database)
            self.repository.recover_interrupted()
            self.assertEqual(self.repository.status(mid).status, 'ready')
            self.assertEqual(client.delete(f'/api/meetings/{mid}').status_code, 204)
        with connect(self.database) as db:
            for table in ['analyses', 'key_points', 'topics', 'action_items', 'topic_sources', 'action_sources', 'key_point_sources', 'utterances']:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_analysis_errors_preserve_transcript_without_partial_analysis(self):
        for error, code in [(LocalError('ollama_unavailable'), 'ollama_unavailable'),
                            (LocalError('ollama_model_missing'), 'ollama_model_missing'),
                            (LocalError('ollama_invalid_response'), 'ollama_invalid_response'),
                            (RuntimeError('PRIVATE'), 'analysis_failed')]:
            with self.subTest(code=code):
                mid = self.upload()
                self.analyzer.analyze.side_effect = error
                self.service.prepare(mid)
                result = self.repository.result(mid)
                self.assertEqual(result.job.status, 'failed')
                self.assertEqual(result.job.error_code, code)
                self.assertNotIn('PRIVATE', result.job.message)
                self.assertEqual(len(result.utterances), 1)
                self.assertFalse(result.analysis_completed)
                self.assertEqual((result.summary, result.topics, result.action_items), ('', [], []))

    def test_analysis_not_called_if_transcription_save_fails(self):
        mid = self.upload()
        with patch.object(self.repository, 'save_transcript', side_effect=sqlite3.OperationalError('PRIVATE')):
            self.service.prepare(mid)
        self.analyzer.analyze.assert_not_called()
        self.assertEqual(self.repository.status(mid).error_code, 'transcript_save_failed')

    def test_analysis_is_atomic_on_database_failure(self):
        mid = self.upload()
        with connect(self.database) as db:
            db.execute("CREATE TRIGGER fail_analysis BEFORE INSERT ON analyses BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        self.service.prepare(mid)
        self.assertEqual(self.repository.status(mid).error_code, 'analysis_failed')
        with connect(self.database) as db:
            for table in ['topics', 'action_items', 'key_points', 'topic_sources']:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.repository.meeting(mid).summary, '')
        self.assertEqual(len(self.repository.result(mid).utterances), 1)

    def test_invalid_fake_adapter_cannot_save_foreign_sources(self):
        mid = self.upload()
        self.analyzer.analyze.side_effect = None
        self.analyzer.analyze.return_value = AnalysisDraft.model_validate(draft_data())
        self.service.prepare(mid)  # Fixture u1 is not an ID in this meeting.
        self.assertEqual(self.repository.status(mid).error_code, 'ollama_invalid_response')
        self.assertFalse(self.repository.result(mid).analysis_completed)

    def test_analyzing_blocks_delete_and_parallel_processing_and_recovers(self):
        mid, second = self.upload(), self.upload()
        self.repository.update(mid, 'analyzing', stage='analysis')
        with self.assertRaises(LocalError) as error:
            self.service.delete(mid)
        self.assertEqual(error.exception.code, 'busy')
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.update(second, 'preparing_audio')
        self.repository.recover_interrupted()
        self.assertEqual(self.repository.status(mid).error_code, 'interrupted')

    def test_prior_transcription_schema_migration_preserves_ready_results(self):
        mid = self.upload()
        self.service.prepare(mid)
        # Recreate only the previous version's jobs table, retaining the actual data.
        with connect(self.database) as db:
            rows = db.execute('SELECT * FROM processing_jobs').fetchall()
            db.execute('DROP TABLE processing_jobs')
            db.execute("CREATE TABLE processing_jobs(id TEXT PRIMARY KEY,meeting_id TEXT,status TEXT CHECK(status IN ('queued','preparing_audio','ready_for_models','transcribing','diarizing','saving_transcript','ready','failed')),stage TEXT,error_code TEXT,audio_path TEXT,detected_language TEXT)")
            db.executemany('INSERT INTO processing_jobs VALUES (?,?,?,?,?,?,?)', rows)
            db.execute("UPDATE processing_jobs SET stage='complete'")
            db.execute('DELETE FROM analyses')
        initialize_database(self.database)
        initialize_database(self.database)
        result = self.repository.result(mid)
        self.assertEqual(result.job.status, 'ready')
        self.assertFalse(result.analysis_completed)
        self.assertEqual(len(result.utterances), 1)
        self.repository.update(mid, 'analyzing')
        self.assertEqual(self.repository.status(mid).status, 'analyzing')
