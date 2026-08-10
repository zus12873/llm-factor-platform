# Backend: API, orchestration and Wind access.
FROM python:3.11-slim

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-dev

COPY backend /app/backend
RUN mkdir -p /data/jobs /data/artifacts /data/runtime && chown -R app /data /app

USER app
EXPOSE 8000
CMD ["/app/backend/.venv/bin/uvicorn", "--factory", \
     "factor_platform.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
