"""Check services without loading any weights or returning private diagnostics."""
from http.client import HTTPConnection, HTTPException
import sys


def healthy(port, path):
    connection = HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        return response.status == 200
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


if __name__ == '__main__':
    sys.exit(0 if healthy(8000, '/api/health') and healthy(11434, '/api/version') else 1)
