"""Local file source; user-supplied names never determine storage paths."""
from pathlib import Path
import re
import shutil
from uuid import UUID

from fastapi import UploadFile

from ..errors import LocalError

EXTENSIONS = {'.mp4', '.m4a', '.mp3', '.wav', '.webm'}


def confined(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    # Refuse reparse points as well as links (Windows junctions included).
    absolute = candidate.absolute()
    if not absolute.is_relative_to(root) or absolute == root:
        raise LocalError('unsafe_path', 409)
    for part in (absolute, *absolute.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise LocalError('unsafe_path', 409)
    resolved = absolute.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise LocalError('unsafe_path', 409)
    return resolved


class MeetingStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, meeting_id: str) -> Path:
        try:
            canonical = str(UUID(meeting_id))
        except ValueError:
            raise LocalError('not_found', 404) from None
        if canonical != meeting_id:
            raise LocalError('not_found', 404)
        return confined(self.root, self.root / canonical)

    def file(self, meeting_id: str, name: str) -> Path:
        directory = self.directory(meeting_id)
        path = confined(self.root, directory / name)
        if path.parent != directory:
            raise LocalError('unsafe_path', 409)
        return path

    async def save(self, meeting_id: str, upload: UploadFile, limit: int):
        name = (upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
        extension = Path(name).suffix.lower()
        if extension not in EXTENSIONS:
            raise LocalError('unsupported_extension', 415)
        original_name = re.sub(r'[^\w .()-]', '_', name)[:180]
        directory = self.directory(meeting_id)
        created = False
        try:
            directory.mkdir(exist_ok=False)
            created = True
            destination = self.file(meeting_id, 'source' + extension)
            size = 0
            with destination.open('xb') as stream:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise LocalError('file_too_large', 413)
                    stream.write(chunk)
            if not size:
                raise LocalError('empty_file', 400)
            return destination, size, original_name
        except (OSError, LocalError) as exc:
            if created:
                try:
                    self.remove(meeting_id)
                except (LocalError, OSError):
                    pass
            if isinstance(exc, LocalError):
                raise
            raise LocalError('save_failed', 500) from None

    def remove(self, meeting_id: str):
        directory = self.directory(meeting_id)
        if not directory.exists():
            return
        # Preflight every descendant: never follow links or junctions during deletion.
        def check(folder: Path):
            for child in folder.iterdir():
                confined(self.root, child)
                if child.is_dir():
                    check(child)
        check(directory)
        shutil.rmtree(directory)
