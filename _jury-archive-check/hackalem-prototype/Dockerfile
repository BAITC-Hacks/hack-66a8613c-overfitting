# syntax=docker/dockerfile:1
ARG OLLAMA_IMAGE=ollama/ollama:0.34.3
FROM ${OLLAMA_IMAGE} AS ollama

FROM node:24-bookworm-slim AS frontend
WORKDIR /build
RUN npm install --global pnpm@11.0.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim-trixie AS app-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data DATABASE_PATH=/app/data/meetings.sqlite3 \
    FRONTEND_DIST=/app/frontend/dist PORT=8000 \
    FFMPEG_PATH=/usr/bin/ffmpeg FFPROBE_PATH=/usr/bin/ffprobe LIBREOFFICE_PATH=/usr/bin/soffice \
    WHISPER_MODEL_PATH=/models/whisper-large-v3 \
    PYANNOTE_MODEL_PATH=/models/speaker-diarization-community-1 \
    OLLAMA_BASE_URL=http://127.0.0.1:11434 OLLAMA_MODEL=qwen3:8b \
    OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/models/ollama \
    OLLAMA_NO_CLOUD=1 OLLAMA_DEBUG=false OLLAMA_NUM_PARALLEL=1 OLLAMA_KEEP_ALIVE=0 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HF_HUB_DISABLE_TELEMETRY=1 PYANNOTE_METRICS_ENABLED=false OTEL_SDK_DISABLED=true \
    HOME=/tmp/home NVIDIA_DRIVER_CAPABILITIES=compute,utility NVIDIA_VISIBLE_DEVICES=all
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 libvulkan1 libopenblas0 ca-certificates libreoffice-writer fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home app
WORKDIR /app
COPY backend/requirements.txt backend/requirements-ml.txt ./backend/
RUN python -m pip install -r backend/requirements.txt
COPY backend/__init__.py ./backend/
COPY backend/app/ ./backend/app/
COPY --from=frontend /build/dist ./frontend/dist/
COPY docker/*.py /opt/container/
# Fail the image build if the local export dependencies or adapter are missing.
RUN python -c "from docx import Document; from backend.app.exporting.documents import ProtocolExporter" \
    && test -x /usr/bin/soffice \
    && test -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
RUN mkdir -p /app/data && chown app:app /app/data

# Small target for API/UI/FFmpeg smoke tests, without downloading CUDA or Ollama.
FROM app-base AS smoke
USER app
CMD ["python", "-m", "backend.app"]

FROM app-base AS runtime
RUN python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 \
    && python -m pip install -r backend/requirements-ml.txt \
    && python -m pip check
COPY --from=ollama /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama /usr/lib/ollama /usr/lib/ollama
USER app
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "/opt/container/healthcheck.py"]
ENTRYPOINT ["python", "/opt/container/start.py"]
