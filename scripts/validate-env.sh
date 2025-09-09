#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${1:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi
missing=0
check_var () {
  local key="$1"
  if ! grep -E "^[[:space:]]*${key}=" "$ENV_FILE" >/dev/null; then
    echo "Missing ${key} in ${ENV_FILE}"
    missing=1
  else
    local val
    val="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | head -n1 | cut -d'=' -f2-)"
    if [[ -z "${val}" ]]; then
      echo "Empty ${key} in ${ENV_FILE}"
      missing=1
    fi
  fi
}
check_var "ARBITRUM_RPC_URL"
check_var "WALLET_ADDRESS"
check_var "HL_ACCOUNT_ADDRESS"
check_var "HL_SECRET_KEY"
if [[ $missing -ne 0 ]]; then
  echo "One or more required variables are missing/empty. Please update ${ENV_FILE}." >&2
  exit 1
fi
if ! grep -E "^[[:space:]]*WALLET_ADDRESS=0x" "$ENV_FILE" >/dev/null; then
  echo "WALLET_ADDRESS should start with 0x" >&2
fi
if grep -E "^[[:space:]]*HL_API_URL=" "$ENV_FILE" >/dev/null; then
  HLURL="$(grep -E "^[[:space:]]*HL_API_URL=" "$ENV_FILE" | head -n1 | cut -d'=' -f2-)"
  if [[ -n "$HLURL" && "$HLURL" != http*://* ]]; then
    echo "HL_API_URL should be a full URL (https://...), or leave it blank." >&2
  fi
fi
echo "Env validation complete."
