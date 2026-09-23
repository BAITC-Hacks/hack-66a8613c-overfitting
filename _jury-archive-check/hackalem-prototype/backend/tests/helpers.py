from pathlib import Path
import tempfile


def temporary_directory():
    """Keep generated test files on the project drive, away from system TEMP."""
    root = Path(__file__).resolve().parents[2] / 'data' / '.test-tmp'
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)
