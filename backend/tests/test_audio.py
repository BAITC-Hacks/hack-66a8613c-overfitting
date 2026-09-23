import json
from pathlib import Path
import subprocess
from backend.tests.helpers import temporary_directory
import unittest
from unittest.mock import patch
import wave

from backend.app.errors import LocalError
from backend.app.processing.audio import AudioPreparer


def write_wav(path, rate=16000, channels=1, seconds=1):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\0' * rate * channels * 2 * seconds)


class AudioTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_directory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / 'source.wav'
        self.target = Path(temp.name) / 'audio.wav'
        self.preparer = AudioPreparer('custom-ffmpeg', 'custom-ffprobe')
        self.info = {'format': {'duration': '1', 'format_name': 'wav'}, 'streams': [{'codec_type': 'audio'}]}
        self.calls = []

    def execute(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0] == 'custom-ffprobe':
            return subprocess.CompletedProcess(args, 0, json.dumps(self.info).encode())
        write_wav(self.target)
        return subprocess.CompletedProcess(args, 0)

    def prepare(self):
        with patch('backend.app.processing.audio.shutil.which', side_effect=lambda value: value), patch('backend.app.processing.audio.subprocess.run', side_effect=self.execute):
            return self.preparer.prepare(self.source, self.target)

    def test_success_and_local_only_arguments(self):
        prepared = self.prepare()
        self.assertEqual(prepared.duration_seconds, 1)
        self.assertEqual(prepared.path, self.target)
        args, kwargs = self.calls[1]
        for flag, value in [('-ac', '1'), ('-ar', '16000'), ('-c:a', 'pcm_s16le'), ('-protocol_whitelist', 'file')]:
            self.assertEqual(args[args.index(flag)+1], value)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(kwargs['timeout'], 300)
        self.assertNotIn('shell', kwargs)

    def test_missing_programs(self):
        for missing in ['custom-ffmpeg', 'custom-ffprobe']:
            with self.subTest(missing=missing), patch('backend.app.processing.audio.shutil.which', side_effect=lambda value: None if value == missing else value), self.assertRaises(LocalError) as error:
                self.preparer.prepare(self.source, self.target)
            self.assertEqual(error.exception.code, missing.removeprefix('custom-') + '_missing')

    def test_invalid_duration_and_no_audio(self):
        for value, expected in [('901', 'duration_exceeded'), ('NaN', 'duration_invalid'), ('0', 'duration_invalid'), ('invalid', 'duration_invalid')]:
            self.info['format']['duration'] = value
            with self.subTest(value=value), self.assertRaises(LocalError) as error:
                self.prepare()
            self.assertEqual(error.exception.code, expected)
        self.info['streams'] = [{'codec_type': 'video'}]
        with self.assertRaises(LocalError) as error:
            self.prepare()
        self.assertEqual(error.exception.code, 'no_audio')

    def test_subprocess_errors_are_sanitized(self):
        for failure, expected in [(subprocess.CalledProcessError(1, ['PRIVATE']), 'probe_failed'), (subprocess.TimeoutExpired(['PRIVATE'], 30), 'processing_timeout'), (OSError('PRIVATE'), 'probe_failed')]:
            with patch('backend.app.processing.audio.shutil.which', return_value='local'), patch('backend.app.processing.audio.subprocess.run', side_effect=failure), self.assertRaises(LocalError) as error:
                self.preparer.prepare(self.source, self.target)
            self.assertEqual(error.exception.code, expected)
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_conversion_failure_and_wrong_output(self):
        probe = subprocess.CompletedProcess([], 0, json.dumps(self.info).encode())
        with patch('backend.app.processing.audio.shutil.which', return_value='local'), patch('backend.app.processing.audio.subprocess.run', side_effect=[probe, subprocess.CalledProcessError(1, ['PRIVATE'])]), self.assertRaises(LocalError) as error:
            self.preparer.prepare(self.source, self.target)
        self.assertEqual(error.exception.code, 'conversion_failed')

    def test_wrong_output_format(self):
        probe = subprocess.CompletedProcess([], 0, json.dumps(self.info).encode())
        write_wav(self.target, rate=8000)
        with patch('backend.app.processing.audio.shutil.which', return_value='local'), patch('backend.app.processing.audio.subprocess.run', return_value=probe), self.assertRaises(LocalError) as error:
            self.preparer.prepare(self.source, self.target)
        self.assertEqual(error.exception.code, 'conversion_failed')

    def test_invalid_probe_json(self):
        with patch('backend.app.processing.audio.shutil.which', return_value='local'), patch('backend.app.processing.audio.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'not json')), self.assertRaises(LocalError) as error:
            self.preparer.prepare(self.source, self.target)
        self.assertEqual(error.exception.code, 'duration_invalid')

    def test_decoded_duration_limit(self):
        self.preparer.max_duration = 1
        def execute(args, **kwargs):
            if args[0] == 'custom-ffprobe':
                return subprocess.CompletedProcess(args, 0, json.dumps(self.info).encode())
            write_wav(self.target, seconds=2)
            return subprocess.CompletedProcess(args, 0)
        with patch('backend.app.processing.audio.shutil.which', side_effect=lambda value: value), patch('backend.app.processing.audio.subprocess.run', side_effect=execute), self.assertRaises(LocalError) as error:
            self.preparer.prepare(self.source, self.target)
        self.assertEqual(error.exception.code, 'duration_exceeded')
