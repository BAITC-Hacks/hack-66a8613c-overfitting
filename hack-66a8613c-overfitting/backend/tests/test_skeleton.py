import importlib
from contextlib import closing
from pathlib import Path
import sqlite3
from backend.tests.helpers import temporary_directory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.database import initialize_database
from backend.app.schemas import Utterance


class SkeletonTests(unittest.TestCase):
    def test_database_models_and_single_worker(self):
        with temporary_directory() as directory:
            path = Path(directory) / 'test.sqlite3'
            initialize_database(path)
            initialize_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('PRAGMA foreign_keys = ON')
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(tables, {'meetings', 'source_files', 'processing_jobs', 'speakers', 'utterances', 'topics', 'metrics', 'problems', 'action_items', 'exports', 'analyses', 'key_points', 'topic_sources', 'action_sources', 'key_point_sources'})
                connection.execute("INSERT INTO meetings(id,title) VALUES ('m','schema test')")
                connection.execute("INSERT INTO processing_jobs(id,meeting_id,status) VALUES ('a','m','preparing_audio')")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO processing_jobs(id,meeting_id,status) VALUES ('b','m','preparing_audio')")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO source_files VALUES ('f','absent','x','x',0,NULL)")
                connection.execute("DELETE FROM meetings WHERE id='m'")
                self.assertEqual(connection.execute('SELECT count(*) FROM processing_jobs').fetchone()[0], 0)

    def test_no_models_required_and_operations_are_honest(self):
        with temporary_directory() as directory:
            from backend.app import config
            settings = patch.multiple(config, DATA_DIR=Path(directory), DATABASE_PATH=Path(directory) / 'test.sqlite3')
            self.addCleanup(settings.stop)
            settings.start()
            from backend.app import main
            importlib.reload(main)
            with TestClient(main.app) as client:
                self.assertEqual(client.get('/api/health').json()['models'], 'checked_per_job')
                responses = [
                    client.patch('/api/meetings/m', json={}),
                    client.get('/api/meetings/m/exports/pdf'), client.get('/api/meetings/m/exports/docx'),
                ]
                for response in responses:
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()['detail']['code'], 'not_found')
                self.assertEqual(client.get('/api/unknown').status_code, 404)
                self.assertEqual(client.get('/api/meetings/m/exports/exe').status_code, 422)
                self.assertEqual(client.get('/openapi.json').status_code, 200)
                if config.FRONTEND_DIST.is_dir():
                    response = client.get('/')
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('text/html', response.headers['content-type'])
                else:
                    self.assertEqual(client.get('/').status_code, 503)
            with closing(sqlite3.connect(config.DATABASE_PATH)) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM meetings').fetchone()[0], 0)

    def test_utterance_time_validation(self):
        with self.assertRaises(ValidationError):
            Utterance(id='u', meeting_id='m', start_seconds=5, end_seconds=1, text='')


if __name__ == '__main__':
    unittest.main()
