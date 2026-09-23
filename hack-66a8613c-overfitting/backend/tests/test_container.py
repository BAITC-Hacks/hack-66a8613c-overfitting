"""Container lifecycle tests; no Docker daemon, network, weights or GPU needed."""
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, 'path', [str(ROOT / 'docker'), *sys.path]):
    spec = importlib.util.spec_from_file_location('container_start', ROOT / 'docker/start.py')
    start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start)
    import healthcheck


class ContainerTests(unittest.TestCase):
    def test_forced_local_configuration_and_cuda_libraries(self):
        with patch.dict(os.environ, {'OLLAMA_HOST': '0.0.0.0:11434', 'OLLAMA_NO_CLOUD': '0'}):
            env = start.environment()
        self.assertEqual(env['OLLAMA_HOST'], '127.0.0.1:11434')
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
        self.assertEqual(env['OLLAMA_NOPRUNE'], 'true')
        self.assertEqual(env['OLLAMA_BASE_URL'], 'http://127.0.0.1:11434')
        self.assertIn('cublas', env['LD_LIBRARY_PATH'])
        self.assertIn('cudnn', env['LD_LIBRARY_PATH'])

    def test_port_conflicts_prevent_start_and_do_not_leak(self):
        with patch.object(start, 'check_ports', side_effect=OSError('PRIVATE')), \
             patch.object(start.subprocess, 'Popen') as popen, \
             patch.object(start.signal, 'signal'), patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(start.main(), 1)
        popen.assert_not_called()
        self.assertNotIn('PRIVATE', stderr.getvalue())

    def test_port_check_only_uses_loopback(self):
        with patch.object(start.socket, 'socket') as factory:
            start.check_ports()
        sock = factory.return_value.__enter__.return_value
        self.assertEqual([call.args[0] for call in sock.bind.call_args_list], [('127.0.0.1', 8000), ('127.0.0.1', 11434)])

    def test_process_exit_stops_both_children(self):
        ollama = Mock(poll=Mock(return_value=None))
        backend = Mock(poll=Mock(return_value=1))
        with patch.object(start, 'check_ports'), patch.object(start.Path, 'mkdir'), \
             patch.object(start.signal, 'signal'), patch.object(start, 'healthy', return_value=True), \
             patch.object(start.subprocess, 'Popen', side_effect=[ollama, backend]) as popen, \
             patch('sys.stderr', new_callable=io.StringIO), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(start.main(), 1)
        ollama.terminate.assert_called_once()
        self.assertEqual(popen.call_args_list[0].args[0], ['ollama', 'serve'])
        self.assertEqual(popen.call_args_list[1].args[0], [sys.executable, '-m', 'backend.app'])
        self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.DEVNULL)

    def test_stop_signal_cleans_up_in_reverse_order(self):
        children = [Mock(poll=Mock(return_value=None)), Mock(poll=Mock(return_value=None))]
        handlers = {}
        def register(sig, handler):
            handlers[sig] = handler
        with patch.object(start, 'check_ports'), patch.object(start.Path, 'mkdir'), \
             patch.object(start.signal, 'signal', side_effect=register), \
             patch.object(start, 'healthy', return_value=True), \
             patch.object(start.subprocess, 'Popen', side_effect=children), \
             patch.object(start.time, 'sleep', side_effect=lambda _: handlers[signal.SIGTERM](None, None)), \
             patch.object(start, 'stop') as stop, patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(start.main(), 0)
        self.assertEqual([call.args[0] for call in stop.call_args_list], list(reversed(children)))

    def test_ollama_timeout_does_not_start_backend(self):
        process = Mock(poll=Mock(return_value=None))
        with patch.object(start, 'check_ports'), patch.object(start.Path, 'mkdir'), \
             patch.object(start.signal, 'signal'), patch.object(start.time, 'monotonic', side_effect=[0, 61]), \
             patch.object(start.subprocess, 'Popen', return_value=process) as popen, \
             patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(start.main(), 1)
        self.assertEqual(popen.call_count, 1)
        process.terminate.assert_called_once()

    def test_stuck_process_is_killed(self):
        process = Mock(poll=Mock(return_value=None))
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 15), 0]
        start.stop(process)
        process.kill.assert_called_once()

    def test_healthcheck_only_calls_loopback_without_models(self):
        with patch.object(healthcheck, 'HTTPConnection') as factory:
            connection = factory.return_value
            connection.getresponse.return_value.status = 200
            self.assertTrue(healthcheck.healthy(11434, '/api/version'))
            factory.assert_called_with('127.0.0.1', 11434, timeout=3)
            connection.request.assert_called_once_with('GET', '/api/version')
            connection.request.side_effect = OSError('PRIVATE')
            self.assertFalse(healthcheck.healthy(8000, '/api/health'))
            self.assertEqual(connection.close.call_count, 2)
