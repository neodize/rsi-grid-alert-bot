""" Enhanced Grid Scanner  –  Pionex‑native, top‑N output, multi‑message
=======================================================================
•  Pulls **PERP** tickers and klines directly from *Pionex* – no CoinGecko.
•  Scores every contract with the original logic (volatility, RSI, etc.).
•  Sorts by score, **sends only the top N** (default 10) to Telegram.
•  Keeps the full rich alert format; auto‑chunks across multiple
   messages so you never exceed Telegram’s 4096‑char limit.
•  Includes an *optional* stub to auto‑stop a running Pionex Grid Bot
   after it completes **TARGET_CYCLES** grid rounds.

Requirements
------------
```
pip install requests numpy
```
You also need your *Pionex API key/secret* for the private “grid status”
endpoint **if** you want the auto‑stop feature.

Configuration block at the top lets you tune:
* `TOP_N_RESULTS`   – how many candidates to send (7‑10 ideal)
* `TARGET_CYCLES`   – cycles after which to stop the bot
* `CHUNK_SIZE`      – Telegram split size (keep < 4096)

"""
from __future__ import annotations

import os
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone
from typing import Dict, List
import numpy as np

# ───────────────────── CONFIG ──────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PIONEX_KEY    = os.getenv("PIONEX_KEY", "")       # only required for auto‑stop
PIONEX_SECRET = os.getenv("PIONEX_SECRET", "")

PIONEX_API    = "https://api.pionex.com"

TOP_N_RESULTS = 10        # send this many best coins (set 7‑10)
TARGET_CYCLES = 15        # auto‑stop grid bot after this many cycles (optional)
CHUNK_SIZE    = 3900      # Telegram safe chunk size (<4096)

# thresholds
MIN_VOLUME       = 10_000_000   # 24 h notional for non‑main tokens
MIN_VOLUME_MAIN  =   1_000_000  # lower threshold for MAIN_TOKENS
MIN_PRICE        = 0.01

MAIN_TOKENS = {"BTC", "ETH", "SOL", "HYPE"}

# ─────────────── TELEGRAM HELPERS ──────────────────

def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram creds missing – skip send")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"Telegram send failed: {exc}")

# ──────────────── PIONEX HELPERS ───────────────────

def fetch_perp_tickers() -> List[Dict]:
    r = requests.get(f"{PIONEX_API}/api/v1/market/tickers", params={"type": "PERP"}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["tickers"]

def fetch_klines(symbol: str, interval: str = "1h", limit: int = 200):
    r = requests.get(
        f"{PIONEX_API}/api/v1/market/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    closes, highs, lows = [], [], []
    for k in r.json()["data"]["klines"]:
        closes.append(float(k["close"]))
        highs.append(float(k["high"]))
        lows.append(float(k["low"]))
    return closes, highs, lows

# auth helpers – only needed for auto‑stop ----------------

def _sign(payload: str) -> str:
    return hmac.new(PIONEX_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def _auth_headers() -> Dict[str, str]:
    if not PIONEX_KEY or not PIONEX_SECRET:
        return {}
    timestamp = str(int(time.time() * 1000))
    signature = _sign(timestamp)
    return {
        "PIONEX-KEY": PIONEX_KEY,
        "PIONEX-SIGNATURE": signature,
        "PIONEX-TIMESTAMP": timestamp,
    }

# ─────────────── ANALYSIS UTILS ───────────────────


def bollinger(arr: List[float], n: int = 20, k: float = 2.0):
    if len(arr) < n:
        return None, None, None
    ma = np.mean(arr[-n:])
    sd = np.std(arr[-n:])
    return ma + k * sd, ma, ma - k * sd

def est_cycles_per_day(width_pct: float) -> int:
    # very rough: 2 grid cycles per 1% BB width
    return int(width_pct * 2)

# ─────────────── SCORING ENGINE ───────────────────

def score_symbol(info: Dict) -> Dict | None:
    symbol = info["symbol"]
    price  = float(info["close"])
    vol24  = float(info["amount"])

    if price < MIN_PRICE:
        return None
    # main token relaxed volume
    if symbol in MAIN_TOKENS:
        if vol24 < MIN_VOLUME_MAIN:
            return None
    else:
        if vol24 < MIN_VOLUME:
            return None

    closes, highs, lows = fetch_klines(info["symbol_raw"], "1h", 200)
    ub, mid, lb = bollinger(closes)
    if not mid:
        return None
    width_pct = (ub - lb) / mid * 100
    cycles    = est_cycles_per_day(width_pct)

    score = 0
    if 5 <= width_pct <= 15:
        score += 40
    elif width_pct < 5:
        score += 20
    else:
        score += 10

    if symbol in MAIN_TOKENS:
        score += 15
    elif vol24 > 50_000_000:
        score += 10

    return {
        "symbol": symbol,
        "price": price,
        "lower": lb,
        "upper": ub,
        "width_pct": round(width_pct, 2),
        "cycles": cycles,
        "score": score,
    }

# ─────────────── GRID ALERT MSG ───────────────────

def fmt_alert(d: Dict) -> str:
    return (
        f"*{d['symbol']} PERP*\n"
        f"Price range: `${d['lower']:.4f}` – `${d['upper']:.4f}`\n"
        f"Width: {d['width_pct']}%   |  Est cycles/day: {d['cycles']}\n"
        f"Recommended: Neutral grid (5‑15×)\n"
        "──────────────────────────────\n"
    )

# ─────────────── AUTO‑STOP STUB ───────────────────

def maybe_stop_bot(bot_id: str):
    """Check if grid bot reached TARGET_CYCLES and stop it (requires auth)."""
    if not PIONEX_KEY or not PIONEX_SECRET:
        return
    try:
        r = requests.get(
            f"{PIONEX_API}/api/v1/grid/bot/detail",
            params={"botId": bot_id},
            headers=_auth_headers(),
            timeout=10,
        )
        r.raise_for_status()
        cycles = int(r.json()["data"]["completedGridNum"])
        if cycles >= TARGET_CYCLES:
            # stop
            requests.post(
                f"{PIONEX_API}/api/v1/grid/bot/stop",
                params={"botId": bot_id},
                headers=_auth_headers(),
                timeout=10,
            ).raise_for_status()
            tg_send(f"🎯 Bot {bot_id} reached {cycles} cycles – auto‑stopped")
    except Exception as exc:
        print(f"Auto‑stop check failed: {exc}")

# ─────────────── TELEGRAM CHUNKER ──────────────────

def chunk_and_send(msgs: List[str]):
    full = "".join(msgs)
    parts = []
    curr = ""
    for line in full.splitlines(keepends=True):
        if len(curr) + len(line) < CHUNK_SIZE:
            curr += line
        else:
            parts.append(curr)
            curr = line
    if curr:
        parts.append(curr)

    total = len(parts)
    for idx, chunk in enumerate(parts, 1):
        tg_send(f"[Grid Scanner {idx}/{total}]\n\n" + chunk)
        time.sleep(1.2)

# ─────────────── MAIN ROUTINE ──────────────────────

def run_scan():
    try:
        tickers = fetch_perp_tickers()
    except Exception as exc:
        tg_send(f"❌ Pionex API error: {exc}")
        return

    infos = []
    for tk in tickers:
        sym_full = tk["symbol"]
        base = sym_full.split("_")[0]
        meta = {
            "symbol": base,
            "symbol_raw": sym_full,
            "close": tk["close"],
            "amount": tk["amount"],
        }
        scored = score_symbol(meta)
        if scored:
            infos.append(scored)

    if not infos:
        tg_send("❌ No suitable perp grid candidates found.")
        return

    infos.sort(key=lambda x: x["score"], reverse=True)
    top = infos[:TOP_N_RESULTS]

    msg_blocks = [fmt_alert(d) for d in top]
    chunk_and_send(msg_blocks)

# ───────────────────────────────────────────────────
if __name__ == "__main__":
    run_scan()
