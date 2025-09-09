import json
import requests
from typing import Any, Dict, Optional, List
from web3 import Web3
from ..utils.erc20 import erc20_at
from ..config import SETTINGS


def _normalize_markets_payload(raw: Any) -> List[dict]:
    if isinstance(raw, list):
        out: List[dict] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                try:
                    obj = json.loads(item)
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:
                    continue
        return out

    if isinstance(raw, dict):
        for key in ("markets", "data", "result"):
            v = raw.get(key)
            if isinstance(v, list):
                return _normalize_markets_payload(v)

    raise RuntimeError("Unrecognized /markets payload shape")


def _normalize_markets_info_payload(raw: Any) -> Dict[str, dict]:
    lst = []
    if isinstance(raw, list):
        lst = raw
    elif isinstance(raw, dict):
        for key in ("markets", "data", "result"):
            v = raw.get(key)
            if isinstance(v, list):
                lst = v
                break
        if not lst:
            lst = [raw]
    else:
        lst = []

    out: Dict[str, dict] = {}
    for item in lst:
        obj = None
        if isinstance(item, dict):
            obj = item
        elif isinstance(item, str):
            try:
                obj = json.loads(item)
            except Exception:
                obj = None
        if not isinstance(obj, dict):
            continue

        mt = None
        for key in ("marketToken", "marketTokenAddress", "address"):
            v = obj.get(key)
            if isinstance(v, str) and v.startswith("0x"):
                mt = v
                break
            if isinstance(v, dict):
                addr = v.get("address")
                if isinstance(addr, str) and addr.startswith("0x"):
                    mt = addr
                    break
        if mt:
            out[mt.lower()] = obj
    return out


def _is_addr(x: Any) -> bool:
    return isinstance(x, str) and x.startswith("0x") and len(x) >= 10


class GMXClient:
    def __init__(self, api_base: str | None = None, rpc_url: str | None = None):
        self.api_base = api_base or SETTINGS.gmx_api_base
        self.session = requests.Session()
        self.w3 = Web3(Web3.HTTPProvider(rpc_url or SETTINGS.arbitrum_rpc_url))

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.api_base}{path}"
        r = self.session.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def list_markets(self) -> List[dict]:
        raw = self._get("/markets")
        return _normalize_markets_payload(raw)

    def markets_info(self) -> Dict[str, dict]:
        raw = self._get("/markets/info")
        return _normalize_markets_info_payload(raw)

    def get_market_info_map(self) -> Dict[str, dict]:
        return self.markets_info()

    def pick_eth_usdc_market(self) -> dict:
        markets = self.list_markets()

        for m in markets:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name", "")).upper()
            if "ETH/USD" in name and "USDC" in name:
                return m

        WETH_ARB = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1".lower()
        USDC_ARB = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831".lower()
        for m in markets:
            if not isinstance(m, dict):
                continue
            idx = m.get("indexToken"); lng = m.get("longToken"); sht = m.get("shortToken")
            if _is_addr(idx) and _is_addr(lng) and _is_addr(sht):
                if str(idx).lower() == WETH_ARB and str(lng).lower() == WETH_ARB and str(sht).lower() == USDC_ARB:
                    return m

        preview = markets[:2]
        raise RuntimeError(f"GMX ETH/USDC market not found; sample: {preview}")

    def market_token_address(self, market: dict) -> str:
        for key in ("marketToken", "marketTokenAddress", "address"):
            v = market.get(key)
            if isinstance(v, str) and v.startswith("0x"):
                return v
            if isinstance(v, dict):
                addr = v.get("address")
                if isinstance(addr, str) and addr.startswith("0x"):
                    return addr
        raise RuntimeError("Could not locate market token address in /markets entry")

    def get_gm_balance_share(self, market_token_addr: str, wallet: str) -> tuple[float, dict]:
        ct = erc20_at(self.w3, market_token_addr)
        wallet_cs = Web3.to_checksum_address(wallet)
        bal = ct.functions.balanceOf(wallet_cs).call()
        ts = ct.functions.totalSupply().call()
        dec = ct.functions.decimals().call()
        share = (bal / (10 ** dec)) / (ts / (10 ** dec)) if ts else 0.0
        return share, {"balance": bal, "totalSupply": ts, "decimals": dec}

    def estimate_pool_long_weth(self, market_info: dict) -> float:
        """Return the pool's long-token amount in ETH units (scaled by decimals)."""
        from ..utils.erc20 import erc20_at

        def _to_int(x):
            try:
                return int(float(x))
            except Exception:
                return None

        raw = _to_int(market_info.get("poolAmountLong"))
        if raw is None:
            for k in ("longTokenAmount", "longAmount", "longTokenBalance"):
                raw = _to_int(market_info.get(k))
                if raw is not None:
                    break
        if raw is None:
            sample_keys = sorted(list(market_info.keys()))[:20]
            raise RuntimeError(f"Could not find long amount field; keys: {sample_keys}")

        long_addr = None
        for k in ("indexToken", "longToken"):
            v = market_info.get(k)
            if isinstance(v, str) and v.startswith("0x"):
                long_addr = v
                break
            if isinstance(v, dict):
                addr = v.get("address")
                if isinstance(addr, str) and addr.startswith("0x"):
                    long_addr = addr
                    break

        dec = 18
        if long_addr:
            ct = erc20_at(self.w3, long_addr)
            try:
                dec = int(ct.functions.decimals().call())
            except Exception:
                dec = 18

        eth_units = raw / (10 ** dec)
        if eth_units > 10_000_000:
            eth_units = eth_units / 1e18
        return float(eth_units)
