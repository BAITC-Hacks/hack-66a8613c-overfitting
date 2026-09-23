from typing import Literal, NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from .schemas import MeetingEdits, MeetingResult, PreparationResult, ProcessingJob
from .sources.multipart import parse_upload
from . import config

router = APIRouter(prefix='/api')


def not_implemented(operation: str) -> NoReturn:
    raise HTTPException(501, detail={
        'code': 'not_implemented', 'operation': operation,
        'message': 'Эта операция пока не реализована.',
    })


@router.get('/health')
def health():
    return {'status': 'ok', 'processing': 'audio_preparation', 'models': 'not_connected'}


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
    repository = request.app.state.service.repository
    return PreparationResult(meeting=repository.meeting(meeting_id), job=repository.status(meeting_id))


@router.patch('/meetings/{meeting_id}', response_model=MeetingResult)
def edit_meeting(meeting_id: str, edits: MeetingEdits):
    not_implemented('edit')


@router.get('/meetings/{meeting_id}/exports/{format}')
def download_export(meeting_id: str, format: Literal['docx', 'pdf']):
    not_implemented('export')


@router.delete('/meetings/{meeting_id}', status_code=204)
def delete_meeting(meeting_id: str, request: Request):
    request.app.state.service.delete(meeting_id)
    return Response(status_code=204)
