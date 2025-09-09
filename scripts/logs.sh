#!/usr/bin/env bash
set -euo pipefail
if command -v docker-compose >/dev/null 2>&1; then
  docker-compose logs -f hedger
elif command -v docker >/dev/null 2>&1; then
  docker compose logs -f hedger
else
  echo "Docker not found. Please install Docker." >&2
  exit 1
fi
