FROM python:3.12-slim

# git clones marketplaces and plugins.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/danielrosehill/Canopy" \
      org.opencontainers.image.licenses="MIT"

EXPOSE 3000
