from contextlib import asynccontextmanager
import sqlite3

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from . import config
from .database import initialize_database
from .errors import LocalError
from .repository import MeetingRepository
from .service import MeetingService
from .sources.storage import MeetingStorage, confined
from .processing.audio import AudioPreparer
from .processing.offline import configure_offline


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_offline()
    storage = MeetingStorage(config.DATA_DIR)
    database = confined(storage.root, config.DATABASE_PATH)
    initialize_database(database)
    repository = MeetingRepository(database)
    repository.recover_interrupted()
    app.state.service = MeetingService(storage, repository, AudioPreparer(config.FFMPEG_PATH, config.FFPROBE_PATH, config.MAX_DURATION_SECONDS))
    yield


# Disable CDN-backed documentation so the application stays self-contained.
app = FastAPI(title='Протокол совещания — подготовка аудио', lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(router)


@app.exception_handler(LocalError)
async def local_error(request, exc: LocalError):
    return JSONResponse(status_code=exc.status, content={'detail': {'code': exc.code, 'message': exc.message}})


@app.exception_handler(sqlite3.Error)
async def database_error(request, exc):
    return JSONResponse(status_code=503, content={'detail': {'code': 'storage_unavailable', 'message': 'Локальное хранилище временно недоступно. Повторите запрос.'}})


@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'], include_in_schema=False)
def unknown_api(path: str):
    return JSONResponse(status_code=404, content={'detail': 'API route not found'})


if config.FRONTEND_DIST.is_dir():
    app.mount('/', StaticFiles(directory=config.FRONTEND_DIST, html=True), name='frontend')
else:
    @app.get('/')
    def frontend_not_built():
        return JSONResponse(status_code=503, content={'detail': 'Frontend not built. See README.md.'})
