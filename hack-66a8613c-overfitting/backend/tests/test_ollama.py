import json
import socket
import unittest
from unittest.mock import Mock, patch

from backend.app.errors import LocalError
from backend.app.processing.analysis import AnalysisDraft, validate_evidence
from backend.app.processing.ollama import OllamaAdapter
from backend.app.processing.offline import _audit, local_inference
from backend.app.schemas import Speaker, Utterance


def transcript():
    return [Utterance(id='u1', meeting_id='m', speaker_id='s', start_seconds=1, end_seconds=3,
                      text='Поручаю подготовить отчёт. Ответственный Иван. Срок в пятницу.')]


def draft_data():
    return {'summary': 'Обсудили подготовку отчёта.', 'key_points': [
        {'direction': 'Отчёт', 'metric': 'не указан', 'problem': 'не указан',
         'source_utterance_ids': ['u1'], 'requires_review': False}], 'topics': [
        {'title': 'Отчёт', 'summary': 'Поручена подготовка отчёта.', 'source_utterance_ids': ['u1'],
         'requires_review': False, 'action_items': [
             {'text': 'подготовить отчёт', 'responsible': 'Иван', 'deadline_original': 'в пятницу',
              'source_utterance_ids': ['u1'], 'requires_review': False}]}]}


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.adapter = OllamaAdapter('http://127.0.0.1:11434', 'qwen3:8b')
        self.connection = Mock()
        self.response = self.connection.getresponse.return_value
        self.response.status = 200
        self.set_response(draft_data())
        patcher = patch('backend.app.processing.ollama.HTTPConnection', return_value=self.connection)
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)

    def set_response(self, data, **extra):
        self.response.read.return_value = json.dumps({'done': True, 'done_reason': 'stop',
            'message': {'content': json.dumps(data)}, **extra}).encode()

    def test_valid_json_and_minimal_payload(self):
        result = self.adapter.analyze(transcript(), [Speaker(id='s', meeting_id='m', label='speaker_1', name='PRIVATE NAME')])
        self.assertEqual(result.topics[0].action_items[0].responsible, 'Иван')
        self.assertTrue(result.topics[0].action_items[0].requires_review)
        self.factory.assert_called_once_with('127.0.0.1', 11434, timeout=300)
        payload = json.loads(self.connection.request.call_args.kwargs['body'])
        records = json.loads(payload['messages'][1]['content'])
        self.assertEqual(set(records[0]), {'id', 'start', 'end', 'speaker', 'text'})
        self.assertEqual(records[0]['speaker'], 'Спикер 1')
        self.assertFalse(payload['stream'])
        self.assertFalse(payload['think'])
        self.assertEqual(payload['format'], AnalysisDraft.model_json_schema())
        self.assertEqual(payload['keep_alive'], 0)
        self.connection.close.assert_called_once()

    def test_invalid_json_schema_and_truncated_response(self):
        for body in [b'PRIVATE', b'[]', b'{"done":true}',
                     json.dumps({'done': True, 'done_reason': 'length', 'message': {'content': '{}'}}).encode(),
                     json.dumps({'done': True, 'done_reason': 'stop', 'message': {'content': '{"summary":2}'}}).encode()]:
            with self.subTest(body=body):
                self.response.read.return_value = body
                with self.assertRaises(LocalError) as error:
                    self.adapter.analyze(transcript(), [])
                self.assertEqual(error.exception.code, 'ollama_invalid_response')
                self.assertNotIn('PRIVATE', str(error.exception))

    def test_http_errors_and_no_redirect_following(self):
        for status, code in [(404, 'ollama_model_missing'), (503, 'ollama_unavailable'), (500, 'analysis_failed'), (302, 'analysis_failed')]:
            self.response.status = status
            with self.assertRaises(LocalError) as error:
                self.adapter.analyze(transcript(), [])
            self.assertEqual(error.exception.code, code)
        self.connection.request.side_effect = TimeoutError('PRIVATE')
        with self.assertRaises(LocalError) as error:
            self.adapter.analyze(transcript(), [])
        self.assertEqual(error.exception.code, 'ollama_unavailable')

    def test_external_configuration_rejected_before_connection(self):
        for url in ['https://example.com', 'http://localhost:11434', 'http://127.0.0.1:11434@evil', 'http://127.0.0.1:8000']:
            with self.assertRaises(LocalError):
                OllamaAdapter(url, 'qwen3:8b').analyze([], [])
        with self.assertRaises(LocalError):
            OllamaAdapter('http://127.0.0.1:11434', 'cloud-model').analyze([], [])
        self.factory.assert_not_called()

    def test_unknown_sources_and_invented_action_or_metric_rejected(self):
        for field, value in [('source_utterance_ids', ['other-meeting']), ('text', 'Несуществующее поручение')]:
            data = draft_data()
            data['topics'][0]['action_items'][0][field] = value
            self.set_response(data)
            with self.assertRaises(LocalError):
                self.adapter.analyze(transcript(), [])
        data = draft_data()
        data['key_points'][0]['metric'] = '999%'
        with self.assertRaises(LocalError):
            validate_evidence(AnalysisDraft.model_validate(data), transcript())

    def test_unsupported_assignee_and_deadline_become_unspecified(self):
        data = draft_data()
        data['topics'][0]['action_items'][0].update(responsible='Спикер 1', deadline_original='2030-01-01')
        item = validate_evidence(AnalysisDraft.model_validate(data), transcript()).topics[0].action_items[0]
        self.assertEqual((item.responsible, item.deadline_original), ('не указан', 'не указан'))
        self.assertTrue(item.requires_review)

    def test_no_actions_are_added_and_empty_transcript_is_honest(self):
        data = draft_data()
        data['topics'][0]['action_items'] = []
        self.set_response(data)
        self.assertEqual(self.adapter.analyze(transcript(), []).topics[0].action_items, [])
        empty = {'summary': '', 'key_points': [], 'topics': []}
        self.set_response(empty)
        self.assertEqual(self.adapter.analyze([], []).model_dump(), empty)
        self.set_response(data)
        with self.assertRaises(LocalError):
            self.adapter.analyze([], [])

    def test_size_limits_fail_without_truncation(self):
        with patch('backend.app.processing.ollama.MAX_INPUT_BYTES', 1), self.assertRaises(LocalError) as error:
            self.adapter.analyze(transcript(), [])
        self.assertEqual(error.exception.code, 'analysis_input_too_large')
        self.factory.assert_not_called()
        with patch('backend.app.processing.ollama.MAX_RESPONSE_BYTES', 1), self.assertRaises(LocalError):
            self.adapter.analyze(transcript(), [])

    def test_network_policy_keeps_external_blocked_and_ml_isolated(self):
        sock = Mock(family=socket.AF_INET)
        _audit('socket.connect', (sock, ('127.0.0.1', 11434)))
        with self.assertRaises(LocalError):
            _audit('socket.connect', (sock, ('203.0.113.1', 80)))
        with local_inference(), self.assertRaises(LocalError):
            _audit('socket.connect', (sock, ('127.0.0.1', 11434)))
