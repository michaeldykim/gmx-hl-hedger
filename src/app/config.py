from pydantic import BaseModel
import os

def _getenv(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)

class Settings(BaseModel):
    # Mode
    dry_run: bool = (_getenv("DRY_RUN", "true").lower() == "true")
    poll_interval_sec: int = int(_getenv("POLL_INTERVAL_SEC", "120"))

    # Hedge tuning
    hedge_factor: float = float(_getenv("HEDGE_FACTOR", "1.0"))
    target_leverage: float = float(_getenv("TARGET_LEVERAGE", "25"))
  
    # GMX
    gmx_api_base: str = _getenv("GMX_API_BASE", "https://arbitrum-api.gmxinfra.io")
    arbitrum_rpc_url: str = _getenv("ARBITRUM_RPC_URL", "")
    arbitrum_chain_id: int = int(_getenv("ARBITRUM_CHAIN_ID", "42161"))
    wallet_address: str = (_getenv("WALLET_ADDRESS", "") or "").lower()

    gm_index_symbol: str = (_getenv("GM_INDEX_SYMBOL", "ETH") or "ETH").upper()
    gm_long_symbol: str = (_getenv("GM_LONG_SYMBOL", "WETH") or "WETH").upper()
    gm_short_symbol: str = (_getenv("GM_SHORT_SYMBOL", "USDC") or "USDC").upper()

    # Hyperliquid
    hl_account_address: str = (_getenv("HL_ACCOUNT_ADDRESS", "") or "").lower()
    hl_secret_key: str = _getenv("HL_SECRET_KEY", "") or ""
    hl_api_url: str = _getenv("HL_API_URL", "") or ""
    hl_chain: str = _getenv("HL_CHAIN", "Mainnet") or "Mainnet"

    # Hedging model control: scales the LP ETH exposure we compute from GMX
    hedge_exposure_scalar: float = float(_getenv("HEDGE_EXPOSURE_SCALAR", "1.0") or "1.0")

    # Debug flags
    hl_debug_dump: bool = (_getenv("HL_DEBUG_DUMP", "false").lower() == "true")

SETTINGS = Settings()
