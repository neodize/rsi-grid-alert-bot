import requests
import logging
from concurrent.futures import ThreadPoolExecutor

PIONEX_API = "https://api.pionex.com"
TIMEFRAME = "1h"
LIMIT = 24
MIN_VOLUME_M_USDT = 1.5
MIN_WIDTH_PCT = 0.75

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def fetch_perp_universe() -> set[str]:
    url = f"{PIONEX_API}/api/v1/common/symbols"
    js = requests.get(url, params={"type": "PERP"}, timeout=10).json()
    return {s["symbol"] for s in js.get("data", [])}


def fetch_perp_tickers() -> list[dict]:
    url = f"{PIONEX_API}/api/v1/market/tickers"
    js = requests.get(url, params={"type": "PERP"}, timeout=10).json()
    return js.get("data", [])


def fetch_klines(symbol: str, interval: str = TIMEFRAME, limit: int = LIMIT) -> list[dict]:
    url = f"{PIONEX_API}/api/v1/market/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        res = requests.get(url, params=params, timeout=10).json()
        if not res.get("data"):
            return []
        return res["data"]
    except Exception:
        return []


def count_price_cycles(prices: list[float]) -> int:
    if len(prices) < 3:
        return 0
    count = 0
    trend = 0  # +1 up, -1 down
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1] and trend != 1:
            count += 1
            trend = 1
        elif prices[i] < prices[i - 1] and trend != -1:
            count += 1
            trend = -1
    return count // 2


def analyse(mode: str = "conservative") -> list[dict]:
    active = fetch_perp_universe()
    tickers = [t for t in fetch_perp_tickers() if t["symbol"] in active]
    results = []

    def worker(ticker):
        symbol = ticker["symbol"]
        price = float(ticker["price"])
        volume = float(ticker["baseVolume24h"]) * price / 1e6  # in million USDT
        if volume < MIN_VOLUME_M_USDT:
            return None

        klines = fetch_klines(symbol)
        if len(klines) < LIMIT:
            return None

        closes = [float(k["close"]) for k in klines]
        high = max(closes)
        low = min(closes)
        width_pct = (high - low) / low * 100
        cycles = count_price_cycles(closes)

        # Conservative: must meet all 3 filters
        if mode == "conservative":
            if width_pct < MIN_WIDTH_PCT or cycles < 4:
                return None
        elif mode == "aggressive":
            if cycles < 2:
                return None

        logging.info(f"{symbol} ✅ {width_pct:.2f}% range, {volume:.2f}M vol, {cycles} cycles")
        return {
            "symbol": symbol,
            "volume_m": volume,
            "range_pct": width_pct,
            "cycles": cycles
        }

    with ThreadPoolExecutor(max_workers=10) as pool:
        for res in pool.map(worker, tickers):
            if res:
                results.append(res)

    return sorted(results, key=lambda x: -x["cycles"])


def format_results(results: list[dict], mode: str) -> str:
    lines = [f"📊 *Grid Scanner* — _{mode}_ mode"]
    for r in results[:10]:
        lines.append(f"• `{r['symbol']}` — {r['range_pct']:.1f}% range, {r['cycles']} swings, {r['volume_m']:.1f}M vol")
    return "\n".join(lines)
