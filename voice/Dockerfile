# canopy-voice: the voice front end. Built from the repo root: docker build -f voice/Dockerfile .
FROM python:3.12-slim

# ffmpeg decodes whatever audio a client sends.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app
COPY voice/pyproject.toml voice/uv.lock ./
COPY voice/src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    VOICE_CONFIG=/config/voice.yaml

LABEL org.opencontainers.image.source="https://github.com/danielrosehill/Canopy" \
      org.opencontainers.image.licenses="MIT"

EXPOSE 8797
CMD ["canopy-voice", "serve"]
