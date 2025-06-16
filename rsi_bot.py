"""Enhanced Grid Scanner – v3.1
================================
Fixes & diagnostics:
1. **Correct ticker endpoint** → `/api/v1/market/tickers?type=PERP`.
2. **Proper symbol conversion** for klines (`BTC_PERP` → `BTC_USDT`).
3. **Detailed debug logging** for every rejection (width, volume, cycles).
4. Adds third fallback mode `loose` (width 2‑30 %, vol ≥ 1 M, cycles ≥ 0.2).
5. Keeps multi‑message Telegram output (3 coins per chunk).

Adjust filters easily via `.env`:
```
SCAN_MODE=conservative   # conservative → aggressive → loose
```
If conservative returns zero, script auto‑tries aggressive; if still zero,
it tries loose.
"""

from __future__ import annotations

import os
import requests
import logging
import time
from typing import Dict, List

import numpy as np

# ───────────────────── CONFIG ──────────────────────
PIONEX_API = "https://api.pionex.com"
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SCAN_MODE        = os.getenv("SCAN_MODE", "conservative").lower()
TOP_N_RESULTS    = 10
CHUNK_SIZE       = 3800  # telegram safe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─────────────── TELEGRAM HELPERS ──────────────────

def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram creds missing – skip send")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        logging.error(f"Telegram send failed: {exc}")

# ─────────────── DATA FETCHERS ────────────────────

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


def fetch_klines(sym_full: str, interval: str = "1h", limit: int = 200):
    if sym_full.endswith("_PERP"):
        spot_sym = sym_full.replace("_PERP", "_USDT")
    else:
        spot_sym = sym_full
    url = f"{PIONEX_API}/api/v1/market/klines"
    rsp = requests.get(url, params={"symbol": spot_sym, "interval": interval, "limit": limit}, timeout=10)
    rsp.raise_for_status()
    js = rsp.json()
    if js.get("code", 0) != 0 or "data" not in js or "klines" not in js["data"]:
        raise RuntimeError(f"Kline fetch failed {sym_full}: {js}")
    closes = [float(k["close"]) for k in js["data"]["klines"]]
    highs  = [float(k["high"])  for k in js["data"]["klines"]]
    lows   = [float(k["low"])   for k in js["data"]["klines"]]
    return closes, highs, lows

# ─────────────── UTILS ────────────────────────────

def bollinger(prices: List[float], n: int = 20, k: float = 2.0):
    if len(prices) < n:
        return None, None, None
    ma = np.mean(prices[-n:])
    sd = np.std(prices[-n:])
    return ma + k * sd, ma, ma - k * sd

def est_cycles(width_pct: float) -> float:
    return width_pct * 2 / 100  # rough 2 cycles per 1% width

# ─────────────── FILTER LOGIC ─────────────────────

FILTERS = {
    "conservative": dict(width=(5, 15), vol=10_000_000, cycles=1.0),
    "aggressive":   dict(width=(3, 25), vol=3_000_000,  cycles=0.5),
    "loose":        dict(width=(2, 30), vol=1_000_000,  cycles=0.2),
}


def passes_filters(width_pct: float, vol: float, cycles: float, mode: str) -> bool:
    f = FILTERS[mode]
    return (f["width"][0] <= width_pct <= f["width"][1] and
            vol >= f["vol"] and
            cycles >= f["cycles"])

# ─────────────── ANALYSIS ─────────────────────────

def analyse(mode: str) -> List[Dict]:
    tickers = fetch_perp_tickers()
    results = []
    for tk in tickers:
        sym = tk["symbol"]
        price = float(tk["close"])
        vol24 = float(tk["amount"])
        try:
            closes, highs, lows = fetch_klines(sym, "1h", 200)
            ub, mid, lb = bollinger(closes)
            if mid is None:
                logging.debug(f"{sym} skipped: insufficient klines")
                continue
            width_pct = (ub - lb) / mid * 100
            cycles = est_cycles(width_pct)
            if passes_filters(width_pct, vol24, cycles, mode):
                results.append({
                    "symbol": sym,
                    "price": price,
                    "lower": lb,
                    "upper": ub,
                    "width": round(width_pct, 2),
                    "cycles": round(cycles, 2),
                })
            else:
                logging.debug(
                    f"{sym} reject mode {mode}: width {width_pct:.2f}, vol {vol24/1e6:.1f}M, cycles {cycles:.2f}")
        except Exception as exc:
            logging.debug(f"{sym} error: {exc}")
    return sorted(results, key=lambda x: -x["cycles"])

# ─────────────── FORMAT & SEND ────────────────────

def fmt_alert(d: Dict) -> str:
    return (
        f"*{d['symbol']}*  |  {d['width']}% width  |  {d['cycles']} cycles/day\n"
        f"Range: `${d['lower']:.4f}` – `${d['upper']:.4f}`\n"
        f"Leverage: 10× Futures\n"
        "───────────────────\n"
    )

def chunk_and_send(msgs: List[str]):
    joined = "".join(msgs)
    parts, curr = [], ""
    for line in joined.splitlines(keepends=True):
        if len(curr) + len(line) < CHUNK_SIZE:
            curr += line
        else:
            parts.append(curr)
            curr = line
    if curr:
        parts.append(curr)

    total = len(parts)
    for i, part in enumerate(parts, 1):
        tg_send(f"📊 Grid Bot Picks {i}/{total}\n\n" + part)
        time.sleep(1.2)

# ─────────────── MAIN FLOW ────────────────────────

def scan_with_fallback():
    modes = [SCAN_MODE, "aggressive", "loose"] if SCAN_MODE == "conservative" else [SCAN_MODE, "loose"]
    for mode in modes:
        logging.info("Scanning in %s mode…", mode)
        picks = analyse(mode)
        if picks:
            chunk_and_send([fmt_alert(p) for p in picks[:TOP_N_RESULTS]])
            if mode != SCAN_MODE:
                tg_send(f"⚠️ No candidates found in {SCAN_MODE} mode. Showing {mode} picks instead.")
            return
    tg_send("⚠️ No suitable grid candidates found in any mode.")

# ───────────────────────────────────────────────────
if __name__ == "__main__":
    scan_with_fallback()
