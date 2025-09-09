# GMX v2 ETH/USDC LP + Hyperliquid Hedge (Docker)

This project automates a simple hedge for a GMX v2 **ETH/USDC** liquidity position by keeping a matching **ETH perpetual short** on **Hyperliquid** so your net ETH exposure stays near zero.

## TL;DR
- Reads your **pool ETH exposure** from GMX v2 (ETH/USDC market).
- Computes **your share** from your GM market token balance.
- Keeps your ETH perp on **Hyperliquid** near **–(pool ETH exposure × HEDGE_FACTOR)**.
- **DRY_RUN=true** by default → prints what it *would* trade, doesn’t place orders.

## Quick Start
1. Install Docker (Docker Desktop on macOS; `docker` on Linux).
2. Copy env and fill it:
   ```bash
   cp .env.example .env
   ```
   Keep `DRY_RUN=true` while testing.
3. Run via Docker:
   ```bash
   docker build -t gmx-hl-hedger .
   docker run --rm --name hedger --env-file .env gmx-hl-hedger
   docker logs -f hedger
   ```
4. Or use docker-compose:
   ```bash
   docker compose up -d --build
   docker compose logs -f hedger
   ```

## What You’ll See (Sample)
```
2025-08-27 03:07:40 | INFO  | src.app.strategy.hedger | GM share=0.000003 | pool_long_eth=14562.419705 | target_short=-0.042746 ETH | current=0.000000 | trade=-0.042746
2025-08-27 03:07:40 | WARN  | src.app.strategy.hedger | [DRY_RUN] Would SELL 0.042746 ETH (reduce_only=False)
2025-08-27 03:07:40 | INFO  | summary                 | target_short=-0.0427, current=0.0000, trade=-0.0427, pool_long_eth=14562.4197, share=0.000003
```

## Files (What each does)
- **Dockerfile** — Builds the container image.
- **requirements.txt** — Python deps.
- **.env.example** — Copy to `.env` and fill.
- **docker-compose.yml** — One-command start/stop with restart policy & log rotation.
- **src/app/main.py** — Entrypoint; scheduler loop.
- **src/app/config.py** — Loads `.env` to `SETTINGS` (includes `HL_DEBUG_DUMP`).
- **src/app/clients/gmx.py** — GMX client (REST + Web3).
- **src/app/clients/hyperliquid.py** — Hyperliquid client with recursive `get_eth_position()`.
- **src/app/strategy/hedger.py** — Hedge logic + safety rails.
- **src/app/utils/erc20.py** — Minimal ERC-20 ABI helper.
- **scripts/** — Start/stop/log/build/restart/validate/dry-run toggle.

## Configuration (`.env`)
```ini
# Mode
DRY_RUN=true
POLL_INTERVAL_SEC=60

# Hedge tuning
HEDGE_FACTOR=1.0
TARGET_LEVERAGE=25
# Optional UI fit: CALIBRATION_FACTOR=1.0  # if you add it in code

# GMX v2 (Arbitrum)
GMX_API_BASE=https://arbitrum-api.gmxinfra.io
ARBITRUM_RPC_URL=
ARBITRUM_CHAIN_ID=42161
WALLET_ADDRESS=
GM_INDEX_SYMBOL=ETH
GM_LONG_SYMBOL=WETH
GM_SHORT_SYMBOL=USDC

# Hyperliquid
HL_ACCOUNT_ADDRESS=
HL_SECRET_KEY=
HL_API_URL=
HL_CHAIN=Mainnet
# Debug
HL_DEBUG_DUMP=false
```

## docker-compose
```yaml
version: "3.9"
services:
  hedger:
    container_name: hedger
    build:
      context: .
      dockerfile: Dockerfile
    image: gmx-hl-hedger:latest
    env_file:
      - .env
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
```

## Helper Scripts (`scripts/`)
Run from repo root; make executable with `chmod +x scripts/*.sh`.

- `scripts/start.sh` — Build & start (compose-aware).
- `scripts/stop.sh` — Stop services.
- `scripts/logs.sh` — Follow logs.
- `scripts/build.sh` — Build image.
- `scripts/restart.sh` — Rebuild & restart.
- `scripts/validate-env.sh [path_to_env]` — Validate required env keys and shapes.
- `scripts/dry-run-toggle.sh [path_to_env] on|off` — Toggle DRY_RUN safely (creates `.bak`).

## Troubleshooting
- Invalid address → ensure `WALLET_ADDRESS` is `0x...`.
- Hyperliquid 422 → chain/base URL mismatch; code uses `exchange.info` internally. Verify `HL_CHAIN`, `HL_API_URL`.
- Huge trades → unit/decimal mismatch; code scales `poolAmountLong` by token decimals and caps trade size.
- Current stays 0 → we now recursively scan `user_state`; enable `HL_DEBUG_DUMP=true` for a preview if needed.

## Security
- Keep `.env` private. DRY_RUN first.
- Consider a dedicated VPS for 24/7.
