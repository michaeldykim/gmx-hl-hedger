#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${1:-.env}"
MODE="${2:-}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi
if [[ "$MODE" != "on" && "$MODE" != "off" ]]; then
  echo "Usage: $0 .env on|off"
  exit 1
fi
if [[ "$MODE" == "on" ]]; then
  sed -i.bak 's/^DRY_RUN=.*/DRY_RUN=true/g' "$ENV_FILE"
  echo "Set DRY_RUN=true in $ENV_FILE"
else
  sed -i.bak 's/^DRY_RUN=.*/DRY_RUN=false/g' "$ENV_FILE"
  echo "Set DRY_RUN=false in $ENV_FILE"
fi
