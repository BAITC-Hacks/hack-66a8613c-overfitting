"""Local configuration; importing this module never contacts model services."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')


def local_path(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default))
    return path if path.is_absolute() else ROOT / path


DATA_DIR = local_path('DATA_DIR', 'data')
DATABASE_PATH = local_path('DATABASE_PATH', str(DATA_DIR / 'meetings.sqlite3'))
FRONTEND_DIST = local_path('FRONTEND_DIST', 'frontend/dist')
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60
FFMPEG_PATH = os.getenv('FFMPEG_PATH', 'ffmpeg')
FFPROBE_PATH = os.getenv('FFPROBE_PATH', 'ffprobe')
