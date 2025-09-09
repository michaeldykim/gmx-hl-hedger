#!/usr/bin/env bash
set -euo pipefail

# Location: scripts/rerun.sh
# Rebuild the image (no cache), restart the service, and tail logs.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"

cd "$root"

if [[ ! -f ".env" ]]; then
  echo "WARNING: .env not found in project root ($root). The container may fail to start."
fi

echo "==> Stopping any running containers..."
docker compose down || true

echo "==> Building image (no cache)..."
docker compose build --no-cache

echo "==> Starting containers..."
docker compose up -d

echo "==> Tailing hedger logs (Ctrl+C to detach)..."
docker compose logs -f hedger
