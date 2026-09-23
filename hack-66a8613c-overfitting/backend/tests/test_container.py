"""Container lifecycle tests; no Docker daemon, network, weights or GPU needed."""
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import runpy
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, 'path', [str(ROOT / 'docker'), *sys.path]):
    spec = importlib.util.spec_from_file_location('container_start', ROOT / 'docker/start.py')
    start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start)
    import healthcheck


class ContainerTests(unittest.TestCase):
    def test_backend_binding_is_loopback_except_explicit_container_mode(self):
        for mode, host in [('0', '127.0.0.1'), ('1', '0.0.0.0')]:
            with patch.dict(os.environ, {'DOCKER_INTERNAL_NETWORK': mode}), patch('uvicorn.run') as run:
                runpy.run_module('backend.app.__main__', run_name='__main__')
            self.assertEqual(run.call_args.kwargs['host'], host)
            self.assertFalse(run.call_args.kwargs['access_log'])

    def test_forced_internal_configuration_and_cuda_libraries(self):
        with patch.dict(os.environ, {'DOCKER_INTERNAL_NETWORK': '1', 'OLLAMA_HOST': 'bad', 'OLLAMA_NO_CLOUD': '0'}):
            env = start.environment()
        self.assertEqual(env['OLLAMA_HOST'], '172.30.66.3:11434')
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
        self.assertEqual(env['OLLAMA_NOPRUNE'], 'true')
        self.assertEqual(env['OLLAMA_BASE_URL'], 'http://172.30.66.3:11434')
        self.assertIn('cublas', env['LD_LIBRARY_PATH'])
        self.assertIn('cudnn', env['LD_LIBRARY_PATH'])

    def test_missing_isolation_flag_or_invalid_role_prevents_start(self):
        for values in ({'DOCKER_INTERNAL_NETWORK': '0'}, {'DOCKER_INTERNAL_NETWORK': '1', 'CONTAINER_SERVICE': 'bad'}):
            with patch.dict(os.environ, values), patch.object(start.subprocess, 'Popen') as popen, \
                 patch.object(start.signal, 'signal'), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(start.main(), 1)
            popen.assert_not_called()

    def test_each_service_starts_only_its_child_and_stops_on_signal(self):
        for role, command in [('backend', [sys.executable, '-m', 'backend.app']), ('ollama', ['ollama', 'serve'])]:
            handlers = {}
            child = Mock(poll=Mock(return_value=None))
            def register(sig, handler):
                handlers[sig] = handler
            with patch.dict(os.environ, {'DOCKER_INTERNAL_NETWORK': '1', 'CONTAINER_SERVICE': role}), \
                 patch.object(start.Path, 'mkdir'), patch.object(start.signal, 'signal', side_effect=register), \
                 patch.object(start.subprocess, 'Popen', return_value=child) as popen, \
                 patch.object(start.time, 'sleep', side_effect=lambda _: handlers[signal.SIGTERM](None, None)), \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(start.main(), 0)
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], command)
            self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.DEVNULL)
            child.terminate.assert_called_once()

    def test_child_failure_and_stuck_process_cleanup(self):
        with patch.dict(os.environ, {'DOCKER_INTERNAL_NETWORK': '1'}), patch.object(start.Path, 'mkdir'), \
             patch.object(start.signal, 'signal'), patch.object(start.subprocess, 'Popen', side_effect=OSError('PRIVATE')), \
             patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(start.main(), 1)
            self.assertNotIn('PRIVATE', stderr.getvalue())
        process = Mock(poll=Mock(return_value=None))
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 15), 0]
        start.stop(process)
        process.kill.assert_called_once()

    def test_healthcheck_targets_only_its_service(self):
        for role, host, port, path in [('ollama', '172.30.66.3', 11434, '/api/version'), ('backend', '127.0.0.1', 8000, '/api/health')]:
            with patch.dict(os.environ, {'CONTAINER_SERVICE': role}), patch.object(healthcheck, 'HTTPConnection') as factory:
                connection = factory.return_value
                connection.getresponse.return_value.status = 200
                self.assertTrue(healthcheck.check_service())
                factory.assert_called_with(host, port, timeout=3)
                connection.request.assert_called_once_with('GET', path)
                connection.request.side_effect = OSError('PRIVATE')
                self.assertFalse(healthcheck.check_service())
                self.assertEqual(connection.close.call_count, 2)
