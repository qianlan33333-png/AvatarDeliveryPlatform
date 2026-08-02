FROM python:3.12-slim AS runtime

ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system avatar && adduser --system --ingroup avatar avatar

COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" .

USER avatar

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
