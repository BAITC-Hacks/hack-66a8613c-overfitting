from datetime import date
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .schemas import MeetingEdits, MeetingResult, ProcessingJob

router = APIRouter(prefix='/api')


def not_implemented(operation: str) -> NoReturn:
    raise HTTPException(501, detail={
        'code': 'not_implemented', 'operation': operation,
        'message': 'Операция пока не реализована. Файлы и результаты не сохраняются.',
    })


@router.get('/health')
def health():
    return {'status': 'ok', 'processing': 'not_implemented'}


@router.post('/meetings', response_model=ProcessingJob, responses={501: {'description': 'Not implemented'}})
async def upload_meeting(
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1)],
    meeting_date: Annotated[date | None, Form()] = None,
    timezone: Annotated[str | None, Form()] = None,
):
    await file.close()
    not_implemented('upload')


@router.get('/meetings/{meeting_id}/status', response_model=ProcessingJob)
def meeting_status(meeting_id: str):
    not_implemented('status')


@router.get('/meetings/{meeting_id}/result', response_model=MeetingResult)
def meeting_result(meeting_id: str):
    not_implemented('result')


@router.patch('/meetings/{meeting_id}', response_model=MeetingResult)
def edit_meeting(meeting_id: str, edits: MeetingEdits):
    not_implemented('edit')


@router.get('/meetings/{meeting_id}/exports/{format}')
def download_export(meeting_id: str, format: Literal['docx', 'pdf']):
    not_implemented('export')


@router.delete('/meetings/{meeting_id}')
def delete_meeting(meeting_id: str):
    not_implemented('delete')
