import unittest
from unittest.mock import patch

from backend.app.database import connect
from backend.tests import test_review as fixtures


class MediaTests(unittest.TestCase):
    setUp = fixtures.ReviewTests.setUp

    def test_full_head_and_single_ranges(self):
        data = b'0123456789'
        self.service.storage.file(self.mid, 'source.wav').write_bytes(data)
        response = self.client.get(self.url + '/media')
        self.assertEqual(response.content, data)
        self.assertEqual(response.headers['content-type'], 'audio/wav')
        self.assertEqual(response.headers['accept-ranges'], 'bytes')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn(str(self.root), str(response.headers))
        for value, expected, span in [('bytes=2-5', b'2345', '2-5'), ('bytes=7-', b'789', '7-9'),
                                      ('bytes=-3', b'789', '7-9'), ('bytes=8-99', b'89', '8-9')]:
            with self.subTest(value=value):
                response = self.client.get(self.url + '/media', headers={'Range': value})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, expected)
                self.assertEqual(response.headers['content-range'], f'bytes {span}/10')
                self.assertEqual(int(response.headers['content-length']), len(expected))
        response = self.client.head(self.url + '/media')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        self.assertEqual(response.headers['content-length'], '10')

    def test_invalid_ranges_missing_media_and_permissions(self):
        for value in ('bytes=99999-', 'bytes=7-2', 'bytes=-0', 'bytes=-', 'bytes=0-1,4-5', 'bytes=PRIVATE'):
            response = self.client.get(self.url + '/media', headers={'Range': value})
            self.assertEqual(response.status_code, 416)
            self.assertIn('content-range', response.headers)
            self.assertNotIn('PRIVATE', response.text)
        self.assertEqual(self.client.get('/api/meetings/missing/media').status_code, 404)
        with patch('backend.app.sources.media.os.fstat', side_effect=OSError('PRIVATE')):
            response = self.client.get(self.url + '/media')
            self.assertEqual(response.status_code, 404)
            self.assertNotIn('PRIVATE', response.text)
        self.service.storage.file(self.mid, 'source.wav').unlink()
        self.assertEqual(self.client.get(self.url + '/media').status_code, 404)

    def test_no_foreign_meeting_paths_or_database_files(self):
        other = fixtures.ready_meeting(self.service)
        for path in [self.root.parent / 'outside.wav', self.root / other.meeting.id / 'source.wav',
                     self.root / 'test.sqlite3', self.root / self.mid / 'export-private.docx']:
            with connect(self.service.repository.database) as db:
                db.execute('UPDATE source_files SET storage_path=? WHERE meeting_id=?', (str(path), self.mid))
            response = self.client.get(self.url + '/media')
            self.assertEqual(response.status_code, 409)
            self.assertNotIn(str(path), response.text)
