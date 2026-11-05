# The CLIs that skills use, so they do not have to be installed on the host. See docs/design.md.
FROM alpine:3.22

RUN apk add --no-cache aws-cli curl git github-cli jq nodejs npm postgresql17-client python3 \
    rclone restic uv

COPY clients/canopy /usr/local/bin/canopy

# The home folder is mounted from agent-data/cli, so CLI logins survive an update.
ENV HOME=/agent-data/cli \
    PATH=/agent-data/cli/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    CANOPY_IN_CLI=1 \
    UV_CACHE_DIR=/tmp/uv \
    npm_config_cache=/tmp/npm

WORKDIR /agent-data

LABEL org.opencontainers.image.source="https://github.com/danielrosehill/Canopy" \
      org.opencontainers.image.licenses="MIT"

CMD ["sleep", "infinity"]
