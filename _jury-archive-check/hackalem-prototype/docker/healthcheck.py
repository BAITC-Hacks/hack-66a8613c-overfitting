"""Check services without loading any weights or returning private diagnostics."""
from http.client import HTTPConnection, HTTPException
import sys
import os


def healthy(port, path, host='127.0.0.1'):
    connection = HTTPConnection(host, port, timeout=3)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        return response.status == 200
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def check_service():
    if os.environ.get('CONTAINER_SERVICE') == 'ollama':
        return healthy(11434, '/api/version', '172.30.66.3')
    return healthy(8000, '/api/health')


if __name__ == '__main__':
    sys.exit(0 if check_service() else 1)
