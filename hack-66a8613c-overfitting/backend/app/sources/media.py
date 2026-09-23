"""Serve an already-open confined source; never accept a filesystem path from HTTP."""
import os
import re
import stat

from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from ..errors import LocalError
from .storage import confined

MEDIA_TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.m4a': 'audio/mp4',
               '.mp3': 'audio/mpeg', '.wav': 'audio/wav'}


def media_response(service, meeting_id: str, range_header: str | None, head: bool = False):
    stream = None
    try:
        # Open while deletion is excluded. The stream owns the file descriptor thereafter.
        with service.review_lock:
            service.repository.meeting(meeting_id)
            path = confined(service.storage.root, service.repository.source(meeting_id))
            if path.parent != service.storage.directory(meeting_id) or path.name != 'source' + path.suffix or path.suffix not in MEDIA_TYPES:
                raise LocalError('unsafe_path', 409)
            stream = path.open('rb')
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise LocalError('unsafe_path', 409)
        size = info.st_size
        headers = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                   'Content-Disposition': f'inline; filename="meeting-{meeting_id}{path.suffix}"'}
        start, end, status = 0, size - 1, 200
        if range_header and not head:
            match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', range_header)
            if not match or not any(match.groups()) or not size:
                stream.close()
                return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            else:
                start, end = max(0, size - int(right)), size - 1
            if start > end or start >= size:
                stream.close()
                return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
            status = 206
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        length = max(0, end - start + 1)
        headers['Content-Length'] = str(length)
        if head:
            stream.close()
            return Response(status_code=200, media_type=MEDIA_TYPES[path.suffix], headers=headers)
        stream.seek(start)

        def chunks():
            try:
                remaining = length
                while remaining:
                    block = stream.read(min(64 * 1024, remaining))
                    if not block:
                        return
                    remaining -= len(block)
                    yield block
            finally:
                stream.close()
        return StreamingResponse(chunks(), status_code=status, media_type=MEDIA_TYPES[path.suffix],
                                 headers=headers, background=BackgroundTask(stream.close))
    except (OSError, LocalError) as error:
        if stream is not None:
            stream.close()
        if isinstance(error, LocalError):
            raise
        raise LocalError('not_found', 404) from None
