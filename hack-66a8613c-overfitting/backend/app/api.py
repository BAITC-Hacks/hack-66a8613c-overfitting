from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from .schemas import MeetingEdits, PreparationResult, ProcessingJob
from .sources.multipart import parse_upload
from .sources.media import media_response
from . import config

router = APIRouter(prefix='/api')


@router.get('/health')
def health():
    return {'status': 'ok', 'processing': 'local_transcription', 'models': 'checked_per_job'}


@router.post('/meetings', status_code=202, response_model=ProcessingJob, openapi_extra={
    'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {
        'type': 'object', 'required': ['file', 'title'], 'properties': {
            'file': {'type': 'string', 'format': 'binary'}, 'title': {'type': 'string'},
            'meeting_date': {'type': 'string', 'format': 'date'}, 'timezone': {'type': 'string'},
        }}}}}})
async def upload_meeting(
    request: Request, background_tasks: BackgroundTasks, form=Depends(parse_upload),
):
    service = request.app.state.service
    job = await service.upload(form['file'], form['title'], form.get('meeting_date'),
                               form.get('timezone'), config.MAX_UPLOAD_BYTES)
    background_tasks.add_task(service.prepare, job.meeting_id)
    return job


@router.get('/meetings/{meeting_id}/status', response_model=ProcessingJob)
def meeting_status(meeting_id: str, request: Request):
    return request.app.state.service.repository.status(meeting_id)


@router.get('/meetings/{meeting_id}/result', response_model=PreparationResult)
def meeting_result(meeting_id: str, request: Request):
    service = request.app.state.service
    with service.review_lock:
        return service.repository.result(meeting_id)


@router.patch('/meetings/{meeting_id}', response_model=PreparationResult)
def edit_meeting(meeting_id: str, edits: MeetingEdits, request: Request):
    return request.app.state.service.edit(meeting_id, edits)


@router.api_route('/meetings/{meeting_id}/media', methods=['GET', 'HEAD'])
def meeting_media(meeting_id: str, request: Request):
    return media_response(request.app.state.service, meeting_id, request.headers.get('range'), request.method == 'HEAD')


@router.get('/meetings/{meeting_id}/exports/{format}')
def download_export(meeting_id: str, format: Literal['docx', 'pdf'], request: Request):
    content, filename = request.app.state.service.export(meeting_id, format)
    mime = 'application/pdf' if format == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return Response(content=content, media_type=mime, headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })


@router.delete('/meetings/{meeting_id}', status_code=204)
def delete_meeting(meeting_id: str, request: Request):
    request.app.state.service.delete(meeting_id)
    return Response(status_code=204)
