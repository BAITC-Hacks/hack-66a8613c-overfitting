from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import DATABASE_PATH, DATA_DIR, FRONTEND_DIST
from .database import initialize_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    initialize_database(DATABASE_PATH)
    yield


# Disable CDN-backed documentation so the application stays self-contained.
app = FastAPI(title='Протокол совещания — каркас', lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(router)


@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'], include_in_schema=False)
def unknown_api(path: str):
    return JSONResponse(status_code=404, content={'detail': 'API route not found'})


if FRONTEND_DIST.is_dir():
    app.mount('/', StaticFiles(directory=FRONTEND_DIST, html=True), name='frontend')
else:
    @app.get('/')
    def frontend_not_built():
        return JSONResponse(status_code=503, content={'detail': 'Frontend not built. See README.md.'})
