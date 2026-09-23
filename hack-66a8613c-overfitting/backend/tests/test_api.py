from pathlib import Path
from backend.tests.helpers import temporary_directory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import config
from backend.app.main import app
from backend.app.database import connect
from backend.app.processing.analysis import AnalysisDraft


class ApiTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_directory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        settings = patch.multiple(config, DATA_DIR=self.root, DATABASE_PATH=self.root / 'meetings.sqlite3')
        settings.start()
        self.addCleanup(settings.stop)
        self.client = self.enterContext(TestClient(app))
        # Upload tests inspect the initial queued state, without invoking FFmpeg.
        self.worker = self.enterContext(patch.object(app.state.service, 'prepare'))

    def upload(self, name='test.wav', body=b'test bytes', **metadata):
        return self.client.post('/api/meetings', data={'title': 'Test', **metadata}, files={'file': (name, body)})

    def test_upload_and_status(self):
        response = self.upload(meeting_date='2026-09-23', timezone='Asia/Qyzylorda')
        self.assertEqual(response.status_code, 202)
        job = response.json()
        self.assertEqual(job['status'], 'queued')
        self.assertTrue((self.root / job['meeting_id'] / 'source.wav').is_file())
        self.assertEqual(self.client.get(f"/api/meetings/{job['meeting_id']}/status").json()['status'], 'queued')
        with connect(config.DATABASE_PATH) as db:
            row = db.execute('SELECT * FROM meetings').fetchone()
            self.assertEqual(row['timezone'], 'Asia/Qyzylorda')

    def test_upload_errors(self):
        for name, data, status, code in [('x.exe', b'x', 415, 'unsupported_extension'), ('x.wav', b'', 400, 'empty_file')]:
            with self.subTest(code=code):
                response = self.upload(name, data)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['detail']['code'], code)
        with patch.object(config, 'MAX_UPLOAD_BYTES', 4):
            response = self.upload(body=b'12345')
        self.assertEqual(response.status_code, 413)
        with connect(config.DATABASE_PATH) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM meetings').fetchone()[0], 0)

    def test_invalid_metadata_and_unknown_id(self):
        for values in [{'timezone': 'not/a/zone'}, {'meeting_date': 'yesterday'}, {'title': '   '}]:
            self.assertEqual(self.upload(**values).status_code, 422)
        self.assertEqual(self.client.get('/api/meetings/missing/status').status_code, 404)

    def test_save_error_does_not_leak(self):
        from backend.app.sources.storage import MeetingStorage
        from backend.app.errors import LocalError
        with patch.object(MeetingStorage, 'save', side_effect=LocalError('save_failed', 500)):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['detail']['code'], 'save_failed')

    def test_oversize_stream_without_content_length(self):
        from starlette.requests import Request
        from backend.app.sources.multipart import parse_upload
        import asyncio
        async def exercise():
            async def receive():
                return {'type': 'http.request', 'body': b'x' * 65541, 'more_body': False}
            request = Request({'type': 'http', 'app': app, 'headers': [(b'content-type', b'multipart/form-data; boundary=x')]}, receive)
            with patch.object(config, 'MAX_UPLOAD_BYTES', 4):
                with self.assertRaises(Exception) as error:
                    await anext(parse_upload(request))
                self.assertEqual(error.exception.code, 'file_too_large')
        asyncio.run(exercise())

    def test_api_preparation_success_and_result(self):
        from backend.app.service import MeetingService
        from backend.app.processing.audio import PreparedAudio
        from backend.tests.test_audio import write_wav
        self.worker.side_effect = lambda meeting_id: MeetingService.prepare(app.state.service, meeting_id)
        def prepare(source, target):
            self.assertEqual(app.state.service.repository.status(source.parent.name).status, 'preparing_audio')
            write_wav(target)
            return PreparedAudio(target, 1)
        from backend.app.processing.contracts import Transcription
        with patch.object(app.state.service.preparer, 'prepare', side_effect=prepare), patch.object(app.state.service.transcriber, 'transcribe', return_value=Transcription(segments=[])), patch.object(app.state.service.diarizer, 'diarize', return_value=[]), patch.object(app.state.service.analyzer, 'analyze', return_value=AnalysisDraft(summary='', key_points=[], topics=[])):
            response = self.upload()
        self.assertEqual(response.json()['status'], 'queued')
        meeting_id = response.json()['meeting_id']
        result = self.client.get(f'/api/meetings/{meeting_id}/result')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['job']['status'], 'ready')
        self.assertTrue(result.json()['models_connected'])
        self.assertEqual(result.json()['utterances'], [])
        self.assertEqual(self.client.delete(f'/api/meetings/{meeting_id}').status_code, 204)
        self.assertFalse((self.root / meeting_id).exists())
        self.assertEqual(self.client.get(f'/api/meetings/{meeting_id}/status').status_code, 404)

    def test_missing_program_status_is_safe(self):
        from backend.app.service import MeetingService
        self.worker.side_effect = lambda meeting_id: MeetingService.prepare(app.state.service, meeting_id)
        for missing in ['ffmpeg', 'ffprobe']:
            with self.subTest(missing=missing), patch('backend.app.processing.audio.shutil.which', side_effect=lambda name: None if name == getattr(config, missing.upper() + '_PATH') else name):
                meeting_id = self.upload().json()['meeting_id']
            status = self.client.get(f'/api/meetings/{meeting_id}/status').json()
            self.assertEqual(status['status'], 'failed')
            self.assertEqual(status['error_code'], missing + '_missing')
            self.assertNotIn(str(self.root), status['message'])

    def test_unknown_result_and_delete_and_unchanged_stubs(self):
        self.assertEqual(self.client.get('/api/meetings/missing/result').status_code, 404)
        self.assertEqual(self.client.delete('/api/meetings/missing').status_code, 404)
        self.assertEqual(self.client.patch('/api/meetings/missing', json={}).status_code, 501)
        for extension in ['pdf', 'docx']:
            self.assertEqual(self.client.get(f'/api/meetings/missing/exports/{extension}').status_code, 501)

    def test_delete_queued_or_preparing_is_blocked(self):
        meeting_id = self.upload().json()['meeting_id']
        for status in ['queued', 'preparing_audio']:
            app.state.service.repository.update(meeting_id, status)
            response = self.client.delete(f'/api/meetings/{meeting_id}')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['detail']['code'], 'busy')
            self.assertTrue((self.root / meeting_id / 'source.wav').exists())

    def test_delete_ignores_tampered_database_paths(self):
        meeting_id = self.upload().json()['meeting_id']
        outside = self.root.parent / (self.root.name + '-sentinel.txt')
        outside.write_text('sentinel', encoding='utf-8')
        self.addCleanup(outside.unlink, missing_ok=True)
        with connect(config.DATABASE_PATH) as db:
            db.execute('UPDATE source_files SET storage_path=?', (str(outside),))
        app.state.service.repository.update(meeting_id, 'failed', 'probe_failed')
        self.assertEqual(self.client.delete(f'/api/meetings/{meeting_id}').status_code, 204)
        self.assertEqual(outside.read_text(), 'sentinel')

    def test_delete_rejects_directory_junction(self):
        # Real Windows junction needs no symlink privilege; Unix uses a symlink.
        import os
        import subprocess
        meeting_id = self.upload().json()['meeting_id']
        app.state.service.repository.update(meeting_id, 'failed', 'probe_failed')
        with temporary_directory() as outside:
            sentinel = Path(outside) / 'sentinel.txt'
            sentinel.write_text('sentinel', encoding='utf-8')
            link = self.root / meeting_id / 'linked'
            if os.name == 'nt':
                subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), outside], check=True, capture_output=True)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                response = self.client.delete(f'/api/meetings/{meeting_id}')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['detail']['code'], 'unsafe_path')
                self.assertTrue(sentinel.exists())
            finally:
                if os.name == 'nt':
                    link.rmdir()
                else:
                    link.unlink()

    def test_delete_filesystem_failure_preserves_row(self):
        meeting_id = self.upload().json()['meeting_id']
        app.state.service.repository.update(meeting_id, 'failed', 'probe_failed')
        with patch.object(app.state.service.storage, 'remove', side_effect=PermissionError('PRIVATE')):
            response = self.client.delete(f'/api/meetings/{meeting_id}')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['detail']['code'], 'delete_failed')
        self.assertEqual(self.client.get(f'/api/meetings/{meeting_id}/status').status_code, 200)

    def test_database_failure_is_safe_and_upload_rolls_back(self):
        import sqlite3
        with patch.object(app.state.service.repository, 'create', side_effect=sqlite3.OperationalError('PRIVATE')):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual([p for p in self.root.iterdir() if p.is_dir() and p.name != '.uploads'], [])
        with patch.object(app.state.service.repository, 'status', side_effect=sqlite3.OperationalError('PRIVATE')):
            response = self.client.get('/api/meetings/test/status')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE', response.text)

    def test_future_results_removed_and_rows_cascade(self):
        meeting_id = self.upload().json()['meeting_id']
        results = self.root / meeting_id / 'results'
        results.mkdir()
        (results / 'placeholder.json').write_text('{}', encoding='utf-8')
        app.state.service.repository.update(meeting_id, 'failed', 'probe_failed')
        self.assertEqual(self.client.delete(f'/api/meetings/{meeting_id}').status_code, 204)
        self.assertFalse(results.exists())
        with connect(config.DATABASE_PATH) as db:
            for table in ['meetings', 'source_files', 'processing_jobs']:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_multipart_spool_uses_data_directory_and_closes(self):
        from tempfile import SpooledTemporaryFile
        from backend.app.sources.multipart import LocalMultipartParser
        with patch.object(LocalMultipartParser, 'spool_max_size', 1), patch('backend.app.sources.multipart.SpooledTemporaryFile', wraps=SpooledTemporaryFile) as factory:
            response = self.upload()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(factory.call_args.kwargs['dir'], self.root / '.uploads')
        self.assertEqual(list((self.root / '.uploads').iterdir()), [])

    def test_malformed_and_duplicate_form_fields(self):
        self.assertEqual(self.client.post('/api/meetings', json={'title': 'Test'}).status_code, 422)
        response = self.client.post('/api/meetings', data={'title': ['first', 'second']}, files={'file': ('test.wav', b'test')})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post('/api/meetings', data={'title': 'Test'}).status_code, 422)
