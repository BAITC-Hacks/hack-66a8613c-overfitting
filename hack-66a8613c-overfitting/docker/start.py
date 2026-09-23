"""Supervise the two local processes; never print model/server diagnostics."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from healthcheck import healthy


def environment():
    env = os.environ.copy()
    env.update(OLLAMA_HOST='127.0.0.1:11434', OLLAMA_NO_CLOUD='1', OLLAMA_DEBUG='false',
               OLLAMA_NUM_PARALLEL='1', OLLAMA_KEEP_ALIVE='0', OLLAMA_NOPRUNE='true',
               OLLAMA_BASE_URL='http://127.0.0.1:11434', OLLAMA_MODEL='qwen3:8b', PORT='8000')
    # CT2 needs the CUDA libraries shipped with the pinned PyTorch wheels.
    libraries = Path(sys.prefix) / 'lib/python3.12/site-packages/nvidia'
    paths = [str(libraries / name / 'lib') for name in ('cublas', 'cudnn')]
    env['LD_LIBRARY_PATH'] = ':'.join(paths + ['/usr/local/nvidia/lib', '/usr/local/nvidia/lib64', env.get('LD_LIBRARY_PATH', '')])
    return env


def check_ports():
    # With host networking an existing host Ollama must never be reused silently.
    for port in (8000, 11434):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    children = []
    stopping = False
    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        check_ports()
        Path(os.environ.get('HOME', '/tmp/home')).mkdir(parents=True, exist_ok=True)
        env = environment()
        ollama = subprocess.Popen(['ollama', 'serve'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append(ollama)
        deadline = time.monotonic() + 60
        while not stopping:
            if ollama.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError()
            if healthy(11434, '/api/version'):
                break
            time.sleep(0.25)
        if stopping:
            return 0
        children.append(subprocess.Popen([sys.executable, '-m', 'backend.app'], env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        print('Local services started. Check container health.', flush=True)
        while not stopping:
            if any(process.poll() is not None for process in children):
                raise RuntimeError()
            time.sleep(0.25)
        return 0
    except Exception:
        print('Container startup/service failure. Check ports, mounts and runtime configuration.', file=sys.stderr)
        return 1
    finally:
        for process in reversed(children):
            stop(process)


if __name__ == '__main__':
    sys.exit(main())
