#!/usr/bin/env sh
set -eu

if docker compose version >/dev/null 2>&1; then
    exec docker compose "$@"
fi

if command -v docker-compose >/dev/null 2>&1; then
    exec docker-compose "$@"
fi

echo "未找到 Docker Compose；请安装 docker compose plugin 或 docker-compose。" >&2
exit 127
