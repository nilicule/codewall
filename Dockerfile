# Single-stage image. UV manages deps; the container runs ONE worker because
# the cache and background harvester live in-process (see README).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first (cached layer) from the locked manifest.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App source.
COPY app.py ./
COPY n2g ./n2g
COPY templates ./templates
COPY static ./static

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GITHUB_ORG=NET2GRID \
    WINDOW_DAYS=90 \
    REFRESH_SECONDS=120

# Cache directory for optional SQLite persistence. A relative
# CACHE_PERSIST_PATH=data/snapshot.sqlite resolves here (WORKDIR /app); mount a
# volume at /app/data to keep the snapshot across container recreations. The
# same relative value also works in local dev, where it resolves under the
# project directory.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 5008

# ONE worker, multiple threads. Do NOT scale workers: each worker would get its
# own in-memory cache and its own harvester. Scale with a CDN in front instead.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:5008", "app:app"]
