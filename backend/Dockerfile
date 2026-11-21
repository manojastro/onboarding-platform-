# Build context is the repository root, so the image can include
# mock_destination as a test dependency (used by the import reliability tests).
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.11.20

WORKDIR /app/backend
COPY mock_destination /app/mock_destination
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen
COPY backend/ ./

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
