"""Supervise one isolated Compose service; never print model/server diagnostics."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time



def environment():
    env = os.environ.copy()
    if env.get('DOCKER_INTERNAL_NETWORK') != '1':
        raise ValueError('Expected isolated Compose network')
    env.update(OLLAMA_HOST='172.30.66.3:11434', OLLAMA_NO_CLOUD='1', OLLAMA_DEBUG='false',
               OLLAMA_NUM_PARALLEL='1', OLLAMA_KEEP_ALIVE='0', OLLAMA_NOPRUNE='true',
               OLLAMA_BASE_URL='http://172.30.66.3:11434', OLLAMA_MODEL='qwen3:8b', PORT='8000')
    # CT2 needs the CUDA libraries shipped with the pinned PyTorch wheels.
    libraries = Path(sys.prefix) / 'lib/python3.12/site-packages/nvidia'
    paths = [str(libraries / name / 'lib') for name in ('cublas', 'cudnn')]
    env['LD_LIBRARY_PATH'] = ':'.join(paths + ['/usr/local/nvidia/lib', '/usr/local/nvidia/lib64', env.get('LD_LIBRARY_PATH', '')])
    return env


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
        env = environment()
        role = env.get('CONTAINER_SERVICE', 'backend')
        if role not in ('backend', 'ollama'):
            raise ValueError('Unknown service')
        Path(env.get('HOME', '/tmp/home')).mkdir(parents=True, exist_ok=True)
        command = ['ollama', 'serve'] if role == 'ollama' else [sys.executable, '-m', 'backend.app']
        children.append(subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        print('Local service started. Check container health.', flush=True)
        while not stopping:
            if any(process.poll() is not None for process in children):
                raise RuntimeError()
            time.sleep(0.25)
        return 0
    except Exception:
        print('Container startup/service failure. Check mounts and runtime configuration.', file=sys.stderr)
        return 1
    finally:
        for process in reversed(children):
            stop(process)


if __name__ == '__main__':
    sys.exit(main())
