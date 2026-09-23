import logging
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from backend.app.errors import LocalError
from backend.app.processing.adapters import FasterWhisperAdapter, PyannoteAdapter, _local_pipeline_config
from backend.app.processing.offline import local_inference
from backend.tests.helpers import temporary_directory


class AdapterTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_directory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.audio = self.root / 'audio.wav'  # Mocked adapters do not read real audio.
        for name in ['model.bin', 'config.json', 'tokenizer.json', 'preprocessor_config.json', 'config.yaml']:
            (self.root / name).write_bytes(b'fixture')
        for component in ['segmentation', 'embedding', 'plda']:
            (self.root / component).mkdir()
            (self.root / component / 'pytorch_model.bin').write_bytes(b'fixture')
        for name in ['xvec_transform.npz', 'plda.npz']:
            (self.root / 'plda' / name).write_bytes(b'fixture')
        self.config = {'pipeline': {'name': 'pyannote.audio.pipelines.SpeakerDiarization',
                                   'params': {key: '$model/' + key for key in ('segmentation', 'embedding', 'plda')}}, 'params': {}}
        self.torch = MagicMock()
        self.torch.cuda.is_available.return_value = True
        self.factory = MagicMock()
        self.modules = {'torch': self.torch,
                        'ctranslate2': SimpleNamespace(get_cuda_device_count=lambda: 1, get_supported_compute_types=lambda device: {'float16'}),
                        'faster_whisper': SimpleNamespace(WhisperModel=self.factory)}

    def test_whisper_local_cuda_and_generator_consumed(self):
        consumed = []
        def segments():
            consumed.append(True)
            logging.getLogger('faster_whisper').debug('PRIVATE')
            yield SimpleNamespace(start=0.0, end=1.0, text='unit fixture')
        self.factory.return_value.transcribe.return_value = (segments(), SimpleNamespace(language='kk'))
        with patch('backend.app.processing.adapters.import_module', side_effect=self.modules.__getitem__):
            result = FasterWhisperAdapter(self.root).transcribe(self.audio)
        self.assertEqual(result.detected_language, 'kk')
        self.assertEqual(len(result.segments), 1)
        self.assertTrue(consumed)
        self.factory.assert_called_once_with(str(self.root.resolve()), device='cuda', compute_type='float16', local_files_only=True)
        options = self.factory.return_value.transcribe.call_args.kwargs
        self.assertIsNone(options['language'])
        self.assertTrue(options['multilingual'])
        self.assertEqual(options['task'], 'transcribe')

    def test_missing_models_fail_before_import(self):
        for adapter, code in [(FasterWhisperAdapter, 'whisper_model_missing'), (PyannoteAdapter, 'pyannote_model_missing')]:
            instance = adapter(self.root / 'absent')
            with patch('backend.app.processing.adapters.import_module') as importer, self.assertRaises(LocalError) as error:
                (instance.transcribe if adapter is FasterWhisperAdapter else instance.diarize)(self.audio)
            self.assertEqual(error.exception.code, code)
            importer.assert_not_called()

    def test_missing_cuda_and_dependencies_are_safe(self):
        self.torch.cuda.is_available.return_value = False
        with patch('backend.app.processing.adapters.import_module', side_effect=self.modules.__getitem__), self.assertRaises(LocalError) as error:
            FasterWhisperAdapter(self.root).transcribe(self.audio)
        self.assertEqual(error.exception.code, 'cuda_unavailable')
        self.factory.assert_not_called()
        with patch('backend.app.processing.adapters.import_module', side_effect=ImportError('PRIVATE')), self.assertRaises(LocalError) as error:
            FasterWhisperAdapter(self.root).transcribe(self.audio)
        self.assertEqual(error.exception.code, 'ml_dependencies_missing')
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_lazy_asr_error_is_sanitized(self):
        def fail():
            raise RuntimeError('PRIVATE')
            yield
        self.factory.return_value.transcribe.return_value = (fail(), SimpleNamespace(language='ru'))
        with patch('backend.app.processing.adapters.import_module', side_effect=self.modules.__getitem__), self.assertRaises(LocalError) as error:
            FasterWhisperAdapter(self.root).transcribe(self.audio)
        self.assertEqual(error.exception.code, 'transcription_failed')
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_pipeline_rejects_remote_and_missing_assets(self):
        resolved = _local_pipeline_config(self.root, self.config)
        self.assertEqual(resolved['pipeline']['params']['embedding']['checkpoint'], str(self.root / 'embedding'))
        self.assertFalse(resolved['pipeline']['params']['embedding']['token'])
        for reference in ['pyannote/remote-model', 'https://example.invalid/model', '$model/missing']:
            self.config['pipeline']['params']['embedding'] = reference
            with self.assertRaises(LocalError) as error:
                _local_pipeline_config(self.root, self.config)
            self.assertEqual(error.exception.code, 'pyannote_model_missing')

    def test_pyannote_runs_local_pipeline_on_cuda(self):
        factory = MagicMock()
        turn = SimpleNamespace(start=0.1, end=0.9)
        factory.from_pretrained.return_value.return_value.speaker_diarization.itertracks.return_value = [(turn, None, 'SPEAKER_00')]
        modules = {**self.modules, 'yaml': SimpleNamespace(safe_load=lambda stream: self.config),
                   'pyannote.audio': SimpleNamespace(Pipeline=factory), 'numpy': MagicMock()}
        stream = MagicMock()
        stream.__enter__.return_value = SimpleNamespace(getnchannels=lambda: 1, getframerate=lambda: 16000,
                                                       getsampwidth=lambda: 2, getnframes=lambda: 0, readframes=lambda n: b'')
        with patch('backend.app.processing.adapters.import_module', side_effect=modules.__getitem__), patch('wave.open', return_value=stream):
            turns = PyannoteAdapter(self.root).diarize(self.audio)
        self.assertEqual(turns[0].speaker, 'SPEAKER_00')
        self.torch.device.assert_called_with('cuda')
        self.assertFalse(factory.from_pretrained.call_args.kwargs['token'])
        self.assertIsInstance(factory.from_pretrained.call_args.args[0], dict)
        self.assertEqual(factory.from_pretrained.return_value.call_args.args[0]['sample_rate'], 16000)

    def test_pyannote_runtime_error_is_safe(self):
        modules = {**self.modules, 'yaml': SimpleNamespace(safe_load=lambda stream: self.config),
                   'pyannote.audio': SimpleNamespace(Pipeline=SimpleNamespace(from_pretrained=MagicMock(side_effect=RuntimeError('PRIVATE'))))}
        with patch('backend.app.processing.adapters.import_module', side_effect=modules.__getitem__), self.assertRaises(LocalError) as error:
            PyannoteAdapter(self.root).diarize(self.audio)
        self.assertEqual(error.exception.code, 'diarization_failed')
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_network_guard_blocks_before_any_connection(self):
        with self.assertRaises(LocalError) as error:
            with local_inference():
                socket.getaddrinfo('example.invalid', 443)
        self.assertEqual(error.exception.code, 'network_forbidden')
        with self.assertRaises(LocalError):
            with local_inference(), socket.socket() as sock:
                sock.connect(('127.0.0.1', 11434))
        # Also prevent background telemetry threads from contacting external addresses.
        with self.assertRaises(LocalError), socket.socket() as sock:
            sock.connect(('192.0.2.1', 443))
