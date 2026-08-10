# Worker: executes signed manifests. No network, no database, no model.
#
# Same image content as the backend but a different entry point and a different
# runtime posture. Kept as its own Dockerfile so the two cannot drift into
# sharing an entry point that assumes credentials the worker must not have.
FROM python:3.11-slim

RUN useradd --create-home --uid 10002 worker
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-dev

COPY backend /app/backend
RUN mkdir -p /data/jobs /data/artifacts && chown -R worker /data /app

USER worker
CMD ["/app/backend/.venv/bin/factor-worker", "serve", \
     "--job-root", "/data/jobs", \
     "--artifact-root", "/data/artifacts", \
     "--input-root", "/data/artifacts/inputs"]
