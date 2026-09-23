"""Bound file bytes while parsing; rolled-over temporary uploads stay in DATA_DIR."""
from tempfile import SpooledTemporaryFile

from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .. import config
from ..errors import LocalError
from .storage import confined


class LocalMultipartParser(MultiPartParser):
    def __init__(self, headers, stream, directory, limit):
        super().__init__(headers, stream, max_files=1, max_fields=3, max_part_size=16384)
        self.directory = directory
        self.limit = limit
        self.file_bytes = 0

    def on_headers_finished(self):
        super().on_headers_finished()
        if self._current_part.file is not None:
            old = self._current_part.file.file
            old.close()
            local = SpooledTemporaryFile(max_size=self.spool_max_size, dir=self.directory)
            self._files_to_close_on_error[-1] = local
            self._current_part.file.file = local

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.limit:
                raise LocalError('file_too_large', 413)
        super().on_part_data(data, start, end)


async def parse_upload(request: Request):
    if not request.headers.get('content-type', '').lower().startswith('multipart/form-data'):
        raise LocalError('invalid_metadata', 422)
    limit = config.MAX_UPLOAD_BYTES
    async def bounded_stream():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit + 65536:
                raise LocalError('file_too_large', 413)
            yield chunk
    try:
        directory = confined(request.app.state.service.storage.root,
                             request.app.state.service.storage.root / '.uploads')
        directory.mkdir(exist_ok=True)
        parser = LocalMultipartParser(request.headers, bounded_stream(), directory, limit)
        form = await parser.parse()
    except MultiPartException:
        raise LocalError('invalid_metadata', 422) from None
    except OSError:
        raise LocalError('save_failed', 500) from None
    try:
        file = form.get('file')
        if not isinstance(file, UploadFile) or not isinstance(form.get('title'), str):
            raise LocalError('invalid_metadata', 422)
        if len(form.multi_items()) != len(form) or set(form) - {'file', 'title', 'meeting_date', 'timezone'}:
            raise LocalError('invalid_metadata', 422)
        yield form
    finally:
        await form.close()
