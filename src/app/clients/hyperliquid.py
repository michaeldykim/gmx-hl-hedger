# src/app/clients/hyperliquid.py
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING, getcontext
from typing import Optional, Tuple, Callable
from urllib.parse import urlparse

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from ..config import SETTINGS

# High precision for Decimal math to avoid float drift
getcontext().prec = 28

# ---- Hyperliquid constraints (perps) ----
# For perps, effective price decimals are bounded by: px_dec_eff = MAX_DECIMALS_PERPS - szDecimals
# HL also caps price formatting to <= 5 significant figures.
MAX_DECIMALS_PERPS = 6
MAX_SIG_FIGS_PERPS = 5


def _resolve_base_url(chain: str, env_url: Optional[str]) -> str:
    """
    Pick API base:
      - If HL_API_URL is set and has http(s) scheme, use it.
      - Else use SDK constants (Mainnet or Testnet).
    """
    if env_url and isinstance(env_url, str) and env_url.startswith(("http://", "https://")):
        return env_url
    return constants.TESTNET_API_URL if str(chain).lower() == "testnet" else constants.MAINNET_API_URL


def _build_exchange(secret: str, account: str, base_url: str) -> Exchange:
    """
    Construct Exchange. Prefer passing a LocalAccount wallet (with sign_message),
    but keep string fallbacks for older SDKs.
    """
    wallet = Account.from_key(secret)  # eth_account.signers.local.LocalAccount

    last_err: Optional[Exception] = None
    attempts: list[Callable[[], Exchange]] = [
        # Preferred signatures (wallet object)
        lambda: Exchange(wallet, account_address=account, base_url=base_url),
        lambda: Exchange(wallet, base_url=base_url, account_address=account),
        lambda: Exchange(wallet=wallet, account_address=account, base_url=base_url),
        # Fallbacks (older SDKs accepted raw secret strings)
        lambda: Exchange(secret, account_address=account, base_url=base_url),
        lambda: Exchange(secret, base_url=base_url, account_address=account),
        lambda: Exchange(secret, base_url, account),
        lambda: Exchange(secret, account, base_url),
        lambda: Exchange(secret, account),
    ]
    for ctor in attempts:
        try:
            ex = ctor()
            if not hasattr(ex, "order") or not hasattr(ex, "info"):
                raise TypeError("Constructed Exchange missing required attributes")
            return ex
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise TypeError(f"Could not construct Exchange with available signatures; last error: {last_err}")


def _round_to_sigfigs(d: Decimal, n: int) -> Decimal:
    """
    Round Decimal d to n significant figures.
    If d is already integral, return as-is to preserve integers.
    """
    if d.is_zero():
        return d
    if d == d.to_integral_value():
        return d
    # e.g., for 1234.56 and n=5 -> shift = -(3 - 4) = 1 => quantize at 10^-1
    shift = - (d.adjusted() - (n - 1))
    return d.quantize(Decimal(1).scaleb(-shift))


class HLClient:
    """
    Hyperliquid client wrapper:
      - Handles SDK quirks
      - Reads ETH perp position robustly
      - Places 'market' orders as limit-IOC with correct precision, sig-fig cap & tick
      - Startup health checks
    """

    def __init__(self, account_address: Optional[str] = None, secret_key: Optional[str] = None) -> None:
        self.account_address: str = (account_address or SETTINGS.hl_account_address or "").lower().strip()
        self.secret_key: str = (secret_key or SETTINGS.hl_secret_key or "").strip()

        if not self.account_address or not self.account_address.startswith("0x"):
            raise ValueError("HL_ACCOUNT_ADDRESS missing or invalid (must start with 0x)")
        if not self.secret_key or not self.secret_key.startswith("0x"):
            raise ValueError("HL_SECRET_KEY missing or invalid (must start with 0x)")

        base_url = _resolve_base_url(SETTINGS.hl_chain, SETTINGS.hl_api_url)
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"HL base_url invalid: {base_url}")

        self.exchange: Exchange = _build_exchange(self.secret_key, self.account_address, base_url)
        self.info = self.exchange.info
        self._asset_idx_cache: dict[str, int] = {}

    # ---------- Position reading ----------

    def get_eth_position(self) -> Tuple[float, float]:
        """
        Return (position_size, avg_entry_price) for ETH perp.
        Walks through user_state to find ETH entry across multiple shapes.
        """
        import logging
        logger = logging.getLogger(__name__)

        def _to_float(x):
            try:
                return float(x)
            except Exception:
                return None

        ETH_ALIASES = {"ETH", "PERP:ETH", "ETH-PERP", "ETHUSD", "ETH-USDC", "ETH/USD"}
        SIZE_KEYS = ("szi", "sz", "size", "position", "positionSize", "pos", "qty")
        ENTRY_KEYS = ("entryPx", "entry_price", "avgEntry", "avg_entry", "entry")

        def is_eth_tag(tag: object) -> bool:
            return isinstance(tag, str) and tag.upper() in ETH_ALIASES

        def extract_from_obj(obj):
            if not isinstance(obj, dict):
                return None, None
            # nested
            pos_obj = obj.get("position")
            if isinstance(pos_obj, dict):
                size = next((_to_float(pos_obj.get(k)) for k in SIZE_KEYS if _to_float(pos_obj.get(k)) is not None), None)
                entry = next((_to_float(pos_obj.get(k)) for k in ENTRY_KEYS if _to_float(pos_obj.get(k)) is not None), None)
                if size is not None:
                    return size, (entry or 0.0)
            # flat
            size = next((_to_float(obj.get(k)) for k in SIZE_KEYS if _to_float(obj.get(k)) is not None), None)
            entry = next((_to_float(obj.get(k)) for k in ENTRY_KEYS if _to_float(obj.get(k)) is not None), None)
            if size is not None:
                return size, (entry or 0.0)
            return None, None

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if is_eth_tag(k):
                        size, entry = extract_from_obj(v)
                        if size is not None:
                            return size, entry
                tag = node.get("asset") or node.get("coin") or node.get("name") or node.get("symbol")
                if is_eth_tag(tag):
                    size, entry = extract_from_obj(node)
                    if size is not None:
                        return size, entry
                for subk in ("assetPositions", "perpPositions", "positions", "openPositions", "perps", "perp"):
                    sub = node.get(subk)
                    if isinstance(sub, (dict, list)):
                        size, entry = walk(sub)
                        if size is not None:
                            return size, entry
                for v in node.values():
                    size, entry = walk(v)
                    if size is not None:
                        return size, entry
            if isinstance(node, list):
                for item in node:
                    size, entry = walk(item)
                    if size is not None:
                        return size, entry
            return None, None

        state = self.info.user_state(self.account_address)
        size, entry = walk(state)
        if size is not None:
            return float(size), float(entry or 0.0)

        if getattr(SETTINGS, "hl_debug_dump", False):
            try:
                logger.debug("HL user_state: %r", str(state)[:1000])
            except Exception:
                pass
        return 0.0, 0.0

    # ---------- Asset / precision helpers ----------

    def _asset_meta(self, symbol: str) -> tuple[int, str, int]:
        """
        Return (index, name, szDecimals) for symbol.
        """
        sym = symbol.upper().strip()
        meta = self.info.meta()
        universe = meta.get("universe", []) if isinstance(meta, dict) else []

        def _name_of(entry):
            if isinstance(entry, str):
                return entry.upper()
            if isinstance(entry, dict):
                n = entry.get("name") or entry.get("symbol") or entry.get("asset")
                return str(n).upper() if isinstance(n, str) else ""
            return ""

        aliases = {sym}
        if sym in ("ETH-PERP", "PERP:ETH", "ETHUSD", "ETH/USDC", "ETH-USDC"):
            aliases.add("ETH")

        for i, entry in enumerate(universe):
            name = _name_of(entry)
            if name in aliases:
                sz_dec = 3
                if isinstance(entry, dict) and isinstance(entry.get("szDecimals"), (int, float)):
                    sz_dec = int(entry["szDecimals"])
                return i, name, sz_dec

        raise RuntimeError(f"Asset not found: {symbol}. Universe={universe}")

    def _asset_px_sz_meta(self, symbol: str) -> tuple[int, str, int, int]:
        """
        Returns (index, NAME, szDecimals, pxDecimals_effective_for_perps).
        HL perps allow at most (MAX_DECIMALS_PERPS - szDecimals) price decimals,
        independent of any pxDecimals-like field you might see.
        """
        idx, name, sz_dec = self._asset_meta(symbol)
        # Base rule for perps
        px_dec_eff = max(0, MAX_DECIMALS_PERPS - int(sz_dec))

        # If SDK meta exposes pxDecimals and it's stricter, respect the stricter one.
        meta = self.info.meta()
        uni = meta.get("universe", []) if isinstance(meta, dict) else []
        if isinstance(idx, int) and 0 <= idx < len(uni):
            ent = uni[idx]
            if isinstance(ent, dict) and isinstance(ent.get("pxDecimals"), (int, float)):
                px_dec_eff = min(px_dec_eff, int(ent["pxDecimals"]))

        return idx, name, int(sz_dec), int(px_dec_eff)

    @staticmethod
    def _round_price_to_tick(px: float, px_dec: int, is_buy: bool) -> float:
        """
        Snap price to exact tick (10^-px_dec) using Decimal to avoid float drift.
        - Buys: round UP (ceil to tick)
        - Sells: round DOWN (floor to tick)
        """
        d_px = Decimal(str(px))
        tick = Decimal(1).scaleb(-px_dec)  # 10^-px_dec, e.g., 0.1 if px_dec=1
        q = d_px / tick
        q_rounded = q.to_integral_value(rounding=(ROUND_CEILING if is_buy else ROUND_FLOOR))
        snapped = q_rounded * tick
        return float(snapped)

    # ---------- Trading ----------

    def place_market_order(self, symbol: str, is_buy: bool, size: float, reduce_only: bool = False):
        """
        Emulate a market order as limit IOC at mid ± slippage (bps), with:
          - price capped to <= 5 significant figures
          - price snapped exactly to tick derived from szDecimals rule
          - size rounded down to szDecimals
        Uses mids-only (no l2_book dependency).
        """
        if size <= 0:
            return {"status": "noop", "reason": "size<=0"}

        # Resolve precisions
        _, coin_name, sz_dec, px_dec = self._asset_px_sz_meta(symbol)

        # Round SIZE to allowed precision (numeric)
        q = Decimal(10) ** (-sz_dec)
        size_dec = (Decimal(abs(size)).quantize(q, rounding=ROUND_DOWN))
        sz_num = float(size_dec)

        # --- Get a mid price from whichever method this SDK exposes ---
        mid: Optional[float] = None
        for meth in ("all_mids", "allMids", "mids"):
            if hasattr(self.info, meth):
                try:
                    mids = getattr(self.info, meth)()   # expected dict-like
                    if isinstance(mids, dict) and coin_name in mids:
                        val = float(mids[coin_name])
                        if val > 0:
                            mid = val
                            break
                except Exception:
                    continue

        if mid is None or mid <= 0:
            raise RuntimeError(
                "Could not fetch mids from Hyperliquid (tried all_mids/allMids/mids). "
                "Please upgrade the hyperliquid SDK to a version that exposes mids."
            )

        # Slippage (bps) using Decimal
        bps = getattr(SETTINGS, "hl_market_slippage_bps", 5) or 5
        slip = Decimal(bps) / Decimal(10_000)

        d_mid = Decimal(str(mid))
        d_raw = d_mid * (Decimal(1) + slip if is_buy else Decimal(1) - slip)

        # Enforce ≤ 5 significant figures (HL rule)
        d_raw = _round_to_sigfigs(d_raw, MAX_SIG_FIGS_PERPS)

        # Tick rounding (Decimal-based)
        limit_px = self._round_price_to_tick(float(d_raw), px_dec, is_buy)

        # ---- Enforce min notional (USD) ----
        # configurable via .env: HL_MIN_NOTIONAL_USD (default 10)
        min_notional = float(getattr(SETTINGS, "hl_min_notional_usd", 10) or 10)
        notional = float(sz_num) * float(limit_px)
        if notional < min_notional:
            # Skip order; too small for venue
            return {
                "status": "skipped_min_notional",
                "min_usd": min_notional,
                "notional": notional,
                "size": float(sz_num),
                "px": float(limit_px),
            }

        order_type = {"limit": {"tif": "Ioc"}}

        # Optional debug: verify divisibility by tick
        try:
            import logging
            d_price = Decimal(str(limit_px))
            tick = Decimal(1).scaleb(-px_dec)
            mod = (d_price % tick)
            logging.getLogger("summary").info(
                "IOC %s %s @ %.*f | mid=%.*f | px_dec=%d sz_dec=%d | tick=%s | (px %% tick)=%s | sigfigs<=%d",
                "BUY" if is_buy else "SELL",
                sz_num,
                px_dec, limit_px,
                px_dec, mid,
                px_dec, sz_dec,
                str(tick),
                str(mod),
                MAX_SIG_FIGS_PERPS,
            )
        except Exception:
            pass

        return self.exchange.order(
            coin_name,     # 'ETH'
            is_buy,
            sz_num,        # numeric size (rounded)
            float(limit_px),
            order_type,    # limit IOC
            reduce_only,
        )

    # ---------- Startup health checks ----------

    def _find_eth_meta(self) -> tuple[int, str, int]:
        """
        Internal: stricter ETH lookup used by health_check. Raises if not found.
        Returns (index, 'ETH', szDecimals).
        """
        meta = self.info.meta()
        if not isinstance(meta, dict):
            raise RuntimeError("HL info.meta() did not return a dict")
        universe = meta.get("universe", [])
        if not isinstance(universe, list) or not universe:
            raise RuntimeError("HL meta.universe is empty")

        for i, entry in enumerate(universe):
            name = ""
            sz_dec = None
            if isinstance(entry, str):
                name = entry.upper()
            elif isinstance(entry, dict):
                n = entry.get("name") or entry.get("symbol") or entry.get("asset")
                if isinstance(n, str):
                    name = n.upper()
                if isinstance(entry.get("szDecimals"), (int, float)):
                    sz_dec = int(entry["szDecimals"])
            if name == "ETH":
                return i, "ETH", (sz_dec if sz_dec is not None else 3)

        raise RuntimeError(f"ETH not present in HL universe: {universe}")

    def health_check(self) -> dict:
        """
        Verifies:
          - Wallet/account wiring works (meta + user_state query)
          - ETH exists in universe and szDecimals resolved
          - Mid price reachable (mids-only)
        Returns a dict summary; raises RuntimeError on hard failures.
        """
        summary = {"wallet": self.account_address, "eth_idx": None, "eth_sz_dec": None, "mid_price": None}

        # 1) Universe & ETH presence
        idx, coin_name, sz_dec = self._find_eth_meta()
        summary["eth_idx"] = idx
        summary["eth_sz_dec"] = sz_dec

        # 2) User state reachable
        _ = self.info.user_state(self.account_address)

        # 3) Mid price reachable
        mid = None
        for meth in ("all_mids", "allMids", "mids"):
            if hasattr(self.info, meth):
                try:
                    mids = getattr(self.info, meth)()
                    if isinstance(mids, dict) and coin_name in mids and float(mids[coin_name]) > 0:
                        mid = float(mids[coin_name])
                        break
                except Exception:
                    continue

        if mid is None or mid <= 0:
            raise RuntimeError("Could not fetch ETH mid price from Hyperliquid (mids API missing or returned <= 0).")

        summary["mid_price"] = mid
        return summary
