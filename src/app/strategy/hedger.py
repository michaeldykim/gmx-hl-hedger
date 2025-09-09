# src/app/strategy/hedger.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from ..config import SETTINGS


logger = logging.getLogger(__name__)
summary = logging.getLogger("summary")


@dataclass
class HedgeReport:
    gm_share: float
    pool_long_eth_raw: float
    pool_long_eth_adj: float
    target_eth_short: float
    current_eth_pos: float
    delta_to_trade: float


class Hedger:
    def __init__(self, gmx_client, hl_client):
        self.gmx = gmx_client
        self.hl = hl_client

    # ---------- GMX helpers (robust to odd shapes) ----------

    def _load_market_entries(self) -> List[Dict[str, Any]]:
        """Coerce GMX markets info to a list[dict] of entries, filtering non-dicts."""
        if hasattr(self.gmx, "get_market_info_map"):
            raw = self.gmx.get_market_info_map()
        elif hasattr(self.gmx, "markets_info"):
            raw = self.gmx.markets_info()
        else:
            raise RuntimeError("GMX: no markets info method (get_market_info_map/markets_info)")

        def flatten_to_list(v: Any) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        out.append(it)
            elif isinstance(v, dict):
                vals = list(v.values())
                if vals and all(isinstance(x, dict) for x in vals):
                    out.extend(vals)
                else:
                    for vv in v.values():
                        out.extend(flatten_to_list(vv))
            return out

        entries = flatten_to_list(raw)

        # Deduplicate by marketToken if present, else by name
        deduped: Dict[str, Dict[str, Any]] = {}
        for e in entries:
            if not isinstance(e, dict):
                continue
            key = None
            mt = e.get("marketToken")
            if isinstance(mt, str) and mt:
                key = f"mt:{mt.lower()}"
            else:
                nm = e.get("name")
                if isinstance(nm, str) and nm:
                    key = f"name:{nm.lower()}"
            if key:
                deduped[key] = e

        result = list(deduped.values()) if deduped else [e for e in entries if isinstance(e, dict)]
        if not result:
            raise RuntimeError("GMX: could not coerce markets info into entries list")
        return result

    @staticmethod
    def _pick_eth_usdc_entry(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Pick ETH/USDC v2 market entry from a list of dict entries."""
        preferred_names = {
            "ETH/USD [ETH-USDC]",
            "ETH-USD [ETH-USDC]",
            "ETH-USD",
            "ETH/USD",
        }
        for e in entries:
            nm = e.get("name")
            if isinstance(nm, str) and nm.strip() in preferred_names:
                return e
        for e in entries:
            nm = e.get("name")
            if isinstance(nm, str):
                up = nm.upper()
                if "ETH" in up and "USD" in up:
                    return e
        for e in entries:
            idx = e.get("indexToken")
            sym = idx.get("symbol") if isinstance(idx, dict) else None
            if isinstance(sym, str) and sym.upper() in {"ETH", "WETH"}:
                return e
        for e in entries:
            lt = e.get("longToken")
            if isinstance(lt, str) and lt.lower().startswith("0x82af"):  # WETH on Arbitrum
                return e
        raise RuntimeError("GMX: ETH/USDC market not found in markets entries")

    def _market_token_from_entry(self, entry: Dict[str, Any]) -> str:
        mt = (entry or {}).get("marketToken")
        if not isinstance(mt, str) or not mt:
            raise RuntimeError("GMX: marketToken missing on ETH/USDC entry")
        return mt

    # ---------- Core calc ----------

    def compute_target_short_eth(self) -> HedgeReport:
        """
        Compute how much ETH to short on HL to hedge the ETH exposure of the GMX v2 ETH/USDC LP.

        Base model:
            raw_pool_long_eth = GMX pool's WETH long amount (WETH units)
            my_share = (my GM tokens / market total supply)
            target_short_raw = - (raw_pool_long_eth * my_share)

        Adjustment:
            pool_long_eth_adj = raw_pool_long_eth * HEDGE_EXPOSURE_SCALAR
            target_short = - (pool_long_eth_adj * my_share)
        """
        entries = self._load_market_entries()
        eth_entry = self._pick_eth_usdc_entry(entries)
        market_token = self._market_token_from_entry(eth_entry)

        # My GM share (fraction)
        if hasattr(self.gmx, "get_gm_balance_share"):
            try:
                gm_share, _bal = self.gmx.get_gm_balance_share(market_token, SETTINGS.wallet_address)
                gm_share = float(gm_share or 0.0)
            except Exception as e:
                logger.warning("GMX: get_gm_balance_share failed: %s", e)
                gm_share = 0.0
        else:
            gm_share = 0.0

        # Pool's ETH long amount (in WETH units) based on market info snapshot
        if hasattr(self.gmx, "estimate_pool_long_weth"):
            try:
                pool_long_eth_raw = float(self.gmx.estimate_pool_long_weth(eth_entry))
            except Exception as e:
                logger.warning("GMX: estimate_pool_long_weth failed: %s", e)
                pool_long_eth_raw = 0.0
        else:
            pool_long_eth_raw = 0.0

        # Apply scalar from config
        scalar = float(SETTINGS.hedge_exposure_scalar or 1.0)
        pool_long_eth_adj = pool_long_eth_raw * scalar

        # Targets
        target_short = - (pool_long_eth_adj * gm_share)

        # Current HL ETH perp position
        cur_pos, _entry_px = self.hl.get_eth_position()
        delta = float(target_short) - float(cur_pos)

        # Detailed logs so you can calibrate precisely
        logger.info(
            "GM share=%0.6f | pool_long_eth(raw)=%0.6f | scalar=%0.4f | pool_long_eth(adj)=%0.6f | "
            "target_short=%+0.6f ETH | current=%0.6f | trade=%+0.6f",
            gm_share, pool_long_eth_raw, scalar, pool_long_eth_adj, target_short, cur_pos, delta
        )
        summary.info(
            "target_short=%+0.4f, current=%+0.4f, trade=%+0.4f, pool_long_eth(raw)=%.4f, adj=%.4f, share=%.6f, scalar=%.4f",
            target_short, cur_pos, delta, pool_long_eth_raw, pool_long_eth_adj, gm_share, scalar
        )

        return HedgeReport(
            gm_share=float(gm_share),
            pool_long_eth_raw=float(pool_long_eth_raw),
            pool_long_eth_adj=float(pool_long_eth_adj),
            target_eth_short=float(target_short),
            current_eth_pos=float(cur_pos),
            delta_to_trade=float(delta),
        )

    # ---------- Trade loop ----------

    def reconcile(self, dry_run: bool = True) -> HedgeReport:
        rep = self.compute_target_short_eth()
        delta = rep.delta_to_trade

        if abs(delta) == 0:
            return rep

        is_buy = delta > 0  # buy to reduce short / add long; sell to add short
        side = "BUY" if is_buy else "SELL"

        if dry_run:
            logger.warning("[DRY_RUN] Would %s %.6f ETH (reduce_only=False)", side, abs(delta))
            return rep

        res = self.hl.place_market_order("ETH", is_buy=is_buy, size=abs(delta), reduce_only=False)

        if isinstance(res, dict) and res.get("status") == "skipped_min_notional":
            summary.info(
                "Skipped order: notional $%.2f < $%.2f (size=%.6f px=%.2f)",
                float(res.get("notional", 0.0)),
                float(res.get("min_usd", 0.0)),
                float(res.get("size", 0.0)),
                float(res.get("px", 0.0)),
            )
            return rep

        logger.info("HL order response: %s", res)
        return rep
