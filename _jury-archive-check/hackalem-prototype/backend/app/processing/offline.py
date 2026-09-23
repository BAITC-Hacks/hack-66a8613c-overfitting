"""Offline ML policy. Never enable an online fallback for a missing local asset."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
import ipaddress
import logging
import os
import socket
import sys
from threading import Lock

from ..errors import LocalError

_inference = ContextVar('local_inference', default=False)
_installed = False
_install_lock = Lock()
DOCKER_OLLAMA_HOST = '172.30.66.3'


def docker_network():
    return os.environ.get('DOCKER_INTERNAL_NETWORK') == '1'


def ollama_host():
    return DOCKER_OLLAMA_HOST if docker_network() else '127.0.0.1'


def _allowed_ollama(host, port):
    return docker_network() and host in (DOCKER_OLLAMA_HOST, DOCKER_OLLAMA_HOST.encode()) and port in (11434, '11434')


def _loopback(host):
    if host in ('localhost', b'localhost'):
        return True
    try:
        return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
    except (ValueError, TypeError):
        return False


def _audit(event, args):
    # Deny external Python socket traffic in all threads, including telemetry threads.
    # Loopback is needed by asyncio/TestClient; inference itself may not use it.
    if event == 'socket.getaddrinfo':
        if _inference.get() or not (_loopback(args[0]) or _allowed_ollama(args[0], args[1])):
            raise LocalError('network_forbidden', 503)
    elif event in ('socket.connect', 'socket.sendto', 'socket.sendmsg'):
        sock, address = args[0], args[-1]
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if _inference.get() or not isinstance(address, tuple) or not (_loopback(address[0]) or _allowed_ollama(address[0], address[1])):
                raise LocalError('network_forbidden', 503)


def configure_offline():
    global _installed
    os.environ.update({
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1',
        'PYANNOTE_METRICS_ENABLED': 'false', 'OTEL_SDK_DISABLED': 'true',
    })
    with _install_lock:
        if not _installed:
            sys.addaudithook(_audit)
            _installed = True


@contextmanager
def local_inference():
    configure_offline()
    marker = _inference.set(True)
    # Libraries can log text at DEBUG, print local paths, or print errors before raising.
    # The worker is sequential; suppress diagnostics during the whole lazy iteration.
    previous = logging.root.manager.disable
    try:
        logging.disable(sys.maxsize)
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            yield
    finally:
        logging.disable(previous)
        _inference.reset(marker)
