"""
Enhanced Grid Scanner – PERP‑aware Scanner (v4.2)
=================================================
- Filters top 10 PERP tokens by 24h volume (plus HYPE)
- Skips wrapped, stable, or excluded tokens
- Recommends Long, Short, or Neutral entries
- Suggests when to stop grid bots (🛑 alert)
- Includes grid count & spacing in Telegram message
"""

import os
import logging
from datetime import datetime
import requests
import numpy as np

# ──────────────── CONFIG ────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PIONEX_API       = "https://api.pionex.com"
INTERVAL_MAP     = {"1h": "60M"}

GRID_FEE_PCT     = 0.05  # 0.05% fee per transaction
GRID_TARGET_SPACING = 0.4  # target 0.4% spacing
GRID_CYCLES_PER_DAY = 8   # average desired cycles/day

WRAPPED_TOKENS = {"WBTC", "WETH", "WSOL", "WBNB", "WMATIC", "WAVAX", "WFTM",
                  "CBBTC", "CBETH", "RETH", "STETH", "WSTETH", "FRXETH", "SFRXETH"}
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "FRAX",
               "FDUSD", "PYUSD", "USDE", "USDB", "LUSD", "SUSD", "DUSD", "OUSD"}
EXCLUDED_TOKENS = {"BTCUP", "BTCDOWN", "ETHUP", "ETHDOWN", "ADAUP", "ADADOWN",
                   "LUNA", "LUNC", "USTC", "SHIB", "DOGE", "PEPE", "FLOKI", "BABYDOGE"}

HYPE = "HYPE_USDT"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ──────────────── HELPERS ───────────────
def is_excluded(sym):
    s = sym.upper()
    return (s in WRAPPED_TOKENS or s in STABLECOINS or s in EXCLUDED_TOKENS or
            s.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def fetch_perp_tickers():
    url = f"{PIONEX_API}/api/v1/market/tickers"
    params = {"type": "PERP"}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["tickers"]

def fetch_klines(symbol, interval="1h", limit=200):
    intr = INTERVAL_MAP.get(interval, interval)
    url = f"{PIONEX_API}/api/v1/market/contract/klines"
    params = {"symbol": symbol, "interval": intr, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data.get("data") or "klines" not in data["data"]:
        raise RuntimeError(f"No klines for {symbol}")
    closes, highs, lows = [], [], []
    for k in data["data"]["klines"]:
        closes.append(float(k["close"]))
        highs.append(float(k["high"]))
        lows.append(float(k["low"]))
    return closes, highs, lows

def analyze(symbol):
    try:
        closes, highs, lows = fetch_klines(symbol)
        if len(closes) < 100:
            return None
        hi, lo = max(closes), min(closes)
        now = closes[-1]
        band = hi - lo
        if band <= 0:
            return None
        pos = (now - lo) / band
        if pos < 0.05 or pos > 0.95:
            return None  # too close to edge
        # Entry zone
        if pos < 0.25:
            entry = "Long"
        elif pos > 0.75:
            entry = "Short"
        else:
            entry = "Neutral"
        # Grid suggestion
        spacing_pct = GRID_TARGET_SPACING
        grid_count = int((band / now * 100) / spacing_pct)
        return {
            "symbol": symbol,
            "price": now,
            "price_range": f"${lo:.8f} – ${hi:.8f}" if now < 1 else f"${lo:,.2f} – ${hi:,.2f}",
            "volatility": f"{(band / now * 100):.1f}%",
            "entry": entry,
            "spacing": f"{spacing_pct:.2f}%",
            "grid_count": grid_count,
        }
    except Exception as e:
        logging.warning(f"Skip {symbol}: {e}")
        return None

def format_alert(d):
    return (
        f"*{d['symbol']}*\n"
        f"💡 Entry Zone: {d['entry']}\n"
        f"Price Range: {d['price_range']}\n"
        f"Grid Count: {d['grid_count']}  |  Spacing: {d['spacing']}\n"
        f"Volatility: {d['volatility']}"
    )

def send_telegram(messages):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    for msg in messages:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
                timeout=10
            ).raise_for_status()
        except Exception as e:
            logging.error(f"Telegram error: {e}")

# ──────────────── MAIN LOOP ─────────────
def main():
    tickers = fetch_perp_tickers()
    sorted_perps = sorted(tickers, key=lambda t: float(t["amount"]), reverse=True)
    top10 = [t["symbol"] for t in sorted_perps if not is_excluded(t["symbol"])][:10]
    if HYPE not in top10:
        top10.append(HYPE)

    # Load last symbols to detect drops
    LAST_FILE = "last_symbols.txt"
    last_seen = set()
    if os.path.exists(LAST_FILE):
        with open(LAST_FILE) as f:
            last_seen = set(line.strip() for line in f.readlines())

    current_valid, messages = [], []
    for sym in top10:
        result = analyze(sym)
        if result:
            current_valid.append(sym)
            messages.append(format_alert(result))

    # Compare to last run
    dropped = sorted(list(last_seen - set(current_valid)))
    for sym in dropped:
        messages.append(f"🛑 *{sym}* dropped out of list – consider stopping the bot.")

    # Save current list
    with open(LAST_FILE, "w") as f:
        f.write("\n".join(current_valid))

    if not messages:
        logging.info("No valid entries found.")
        return

    # Chunk messages to fit Telegram limit
    chunks, buf = [], ""
    for m in messages:
        if len(buf) + len(m) + 2 > 4000:
            chunks.append(buf)
            buf = m + "\n\n"
        else:
            buf += m + "\n\n"
    if buf:
        chunks.append(buf)
    send_telegram(chunks)

if __name__ == "__main__":
    main()
