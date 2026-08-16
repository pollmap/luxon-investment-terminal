FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /workspace

COPY pyproject.toml README.md ./
COPY api ./api
COPY backend ./backend
COPY data ./data
COPY db ./db
COPY mcp ./mcp
COPY packages ./packages
COPY services ./services

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[warehouse,migration]" \
    && mkdir -p \
        /workspace/data/raw \
        /workspace/data/warehouse \
        /workspace/storage/cache \
        /workspace/storage/chart_layouts \
        /workspace/storage/chart_runs \
        /workspace/storage/imports \
        /workspace/storage/rendered_charts

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic -c db/alembic.ini upgrade head && exec python -m uvicorn services.api.main:app --host 0.0.0.0 --port 8000"]
