"""enhanced_grid_scanner.py — Pionex‑native v3 (robust)
=====================================================
Fixes the latest KeyError (`symbols`) by **removing the deprecated
`/exchangeInfo` call**. We now build the universe directly from
`/market/tickers?type=PERP`, which is stable and returns
`{"data":{"tickers":[...]}}`.

Key changes
-----------
1. **get_perp_tickers()** — pulls all perpetual contracts with volume
   data; no more missing `symbols` key.
2. **Kline symbol fix** — converts e.g. `BTC_PERP` → `BTC_USDT` when
   calling `/market/klines`.
3. **Error handling** — if Pionex returns `{code:400,...}` or missing
   `data`, the function raises a clear exception.
4. **Telegram chunk send** unchanged (3 per message, original format).
5. Still limits output to top N (default 10) by score.

Drop‑in replacement, no other changes required.
"""

from __future__ import annotations

import os
import time
import hmac
import hashlib
import logging
from typing import Dict, List

import requests
import numpy as np

# ───────────────────── CONFIG ──────────────────────
PIONEX_API = "https://api.pionex.com"
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TOP_N_RESULTS = 10          # show best N coins (≤ 10 keeps messages tidy)
CHUNK_SIZE    = 3900        # Telegram safety (<4096)

MIN_VOL_24H   = 10_000_000  # ignore illiquid contracts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─────────────── TELEGRAM HELPER ──────────────────

def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram credentials missing – skip send")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        ).raise_for_status()
    except requests.exceptions.RequestException as exc:
        logging.error(f"Telegram send failed: {exc}")

# ─────────────── PIONEX HELPERS ───────────────────

def fetch_perp_tickers() -> List[Dict]:
    url = f"{PIONEX_API}/api/v1/market/tickers"
    try:
        rsp = requests.get(url, params={"type": "PERP"}, timeout=10)
        rsp.raise_for_status()
        js = rsp.json()
    except Exception as exc:
        raise RuntimeError(f"Ticker fetch error: {exc}") from exc

    if js.get("code", 0) != 0 or "data" not in js or "tickers" not in js["data"]:
        raise RuntimeError(f"Unexpected ticker payload: {js}")
    return js["data"]["tickers"]


def fetch_klines(symbol: str, interval: str = "1h", limit: int = 200):
    # Pionex wants spot‑style symbol for klines (“BTC_USDT”) even for perp
    if symbol.endswith("_PERP"):
        symbol = symbol.replace("_PERP", "_USDT")

    url = f"{PIONEX_API}/api/v1/market/klines"
    rsp = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
    rsp.raise_for_status()
    js = rsp.json()
    if js.get("code", 0) != 0 or "data" not in js or "klines" not in js["data"]:
        raise RuntimeError(f"Kline fetch failed for {symbol}: {js}")

    closes, highs, lows = [], [], []
    for k in js["data"]["klines"]:
        closes.append(float(k["close"]))
        highs.append(float(k["high"]))
        lows.append(float(k["low"]))
    return closes, highs, lows

# ─────────────── ANALYTICS UTILS ───────────────────

def bollinger(prices: List[float], n: int = 20, k: float = 2.0):
    if len(prices) < n:
        return None, None, None
    ma = np.mean(prices[-n:])
    sd = np.std(prices[-n:])
    return ma + k * sd, ma, ma - k * sd


def est_cycles(width_pct: float) -> int:
    return int(width_pct * 2)  # rough: 2 cycles per 1% band width

# ─────────────── SCORE + FORMAT ───────────────────

def score_contract(tk: Dict) -> Dict | None:
    sym_full = tk["symbol"]
    price = float(tk["close"])
    vol24 = float(tk["amount"])

    if vol24 < MIN_VOL_24H or price <= 0:
        return None

    try:
        closes, highs, lows = fetch_klines(sym_full, "1h", 200)
        ub, mid, lb = bollinger(closes)
    except Exception as exc:
        logging.debug(f"{sym_full} kline error: {exc}")
        return None

    if mid is None:
        return None
    width_pct = (ub - lb) / mid * 100
    cycles = est_cycles(width_pct)

    score = cycles
    return {
        "symbol": sym_full,
        "price": price,
        "lower": lb,
        "upper": ub,
        "width": round(width_pct, 2),
        "cycles": cycles,
        "score": score,
    }


def fmt_alert(d: Dict) -> str:
    return (
        f"*{d['symbol']}*\n"
        f"Range: `${d['lower']:.4f}` – `${d['upper']:.4f}`  ({d['width']}% width)\n"
        f"Est cycles/day: `{d['cycles']}`   |   Leverage: 10×\n"
        "───────────────────────────\n"
    )

# ─────────────── CHUNK + SEND ────────────────────

def chunk_send(blocks: List[str]):
    joined = "".join(blocks)
    chunks, curr = [], ""
    for line in joined.splitlines(keepends=True):
        if len(curr) + len(line) < CHUNK_SIZE:
            curr += line
        else:
            chunks.append(curr);
            curr = line
    if curr:
        chunks.append(curr)

    total = len(chunks)
    for i, ch in enumerate(chunks, 1):
        tg_send(f"[Grid Bot Scan {i}/{total}]\n\n" + ch)
        time.sleep(1.2)

# ─────────────── MAIN LOGIC ───────────────────────

def run():
    logging.info("Scanning Pionex perpetual contracts…")
    try:
        tickers = fetch_perp_tickers()
    except Exception as exc:
        tg_send(f"❌ Ticker fetch failed: {exc}")
        return

    scored = []
    for tk in tickers:
        info = score_contract(tk)
        if info:
            scored.append(info)

    if not scored:
        tg_send("❌ No suitable grid candidates found.")
        return

    scored.sort(key=lambda x: x["score"], reverse=True)
    topN = scored[:TOP_N_RESULTS]
    msg_blocks = [fmt_alert(d) for d in topN]

    chunk_send(msg_blocks)
    logging.info("Scan finished – sent %d alerts", len(topN))

# ───────────────────────────────────────────────────
if __name__ == "__main__":
    run()
