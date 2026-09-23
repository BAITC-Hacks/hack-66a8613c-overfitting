import os

import uvicorn

from . import config  # Load the repository .env before reading PORT.

if __name__ == '__main__':
    uvicorn.run('backend.app.main:app', host='127.0.0.1', port=int(os.getenv('PORT', '8000')), access_log=False)
