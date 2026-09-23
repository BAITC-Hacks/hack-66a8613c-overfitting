import os

import uvicorn

from . import config  # Load the repository .env before reading PORT.
from .processing.offline import docker_network

if __name__ == '__main__':
    # All interfaces only inside the isolated Compose network; host publication is loopback.
    uvicorn.run('backend.app.main:app', host='0.0.0.0' if docker_network() else '127.0.0.1',
                port=int(os.getenv('PORT', '8000')), access_log=False)
