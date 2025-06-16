"""
Enhanced Grid Scanner – PERP‑aware Scanner (v4.1)
=================================================
- Filters only top 10 PERP tokens by 24h volume (plus HYPE)
- Excludes wrapped, stable, and undesirable tokens
- Only includes tokens in mid‑range zone (safe entry)
- Sends Telegram alerts in compact, multi-message format
"""

import os
import logging
from datetime import datetime, timezone
import requests
import numpy as np

# ──────────────── CONFIG ─────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PIONEX_API       = "https://api.pionex.com"
INTERVAL_MAP     = {"1h": "60M", "4h": "4H", "1d": "1D"}

# ──────────────── CONSTANTS ─────────────
WRAPPED_TOKENS = {"WBTC", "WETH", "WSOL", "WBNB", "WMATIC", "WAVAX", "WFTM",
                  "CBBTC", "CBETH", "RETH", "STETH", "WSTETH", "FRXETH", "SFRXETH"}
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "FRAX",
              "FDUSD", "PYUSD", "USDE", "USDB", "LUSD", "SUSD", "DUSD", "OUSD"}
EXCLUDED_TOKENS = {"BTCUP", "BTCDOWN", "ETHUP", "ETHDOWN", "ADAUP", "ADADOWN",
                   "LUNA", "LUNC", "USTC", "SHIB", "DOGE", "PEPE", "FLOKI", "BABYDOGE"}
HYPE = "HYPE_USDT_PERP"

# ──────────────── LOGGING ───────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ──────────────── HELPERS ───────────────
def sma(arr, n): return sum(arr[-n:]) / n if len(arr) >= n else None

def atr(highs, lows, closes, n=14):
    tr = [max(h-l, abs(h-c), abs(l-c)) for h, l, c in zip(highs, lows, closes)]
    return sma(tr, n)

def is_excluded(sym):
    s = sym.upper()
    return (s in WRAPPED_TOKENS or s in STABLECOINS or s in EXCLUDED_TOKENS or
            s.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

# ──────── PIONEX API WRAPPERS ──────────
def fetch_perp_tickers():
    rsp = requests.get(f"{PIONEX_API}/api/v1/market/tickers", params={"type": "PERP"}, timeout=10)
    rsp.raise_for_status()
    return rsp.json()["data"]["tickers"]

def fetch_klines(symbol, interval="1h", limit=200):
    intr = INTERVAL_MAP.get(interval, interval)
    url = f"{PIONEX_API}/api/v1/market/contract/klines"
    params = {"symbol": symbol, "interval": intr, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    js = r.json()
    if not js.get("data") or "klines" not in js["data"]:
        raise RuntimeError(f"Klines unavailable for {symbol} {interval}")
    closes, vols, highs, lows = [], [], [], []
    for k in js["data"]["klines"]:
        closes.append(float(k["close"]))
        vols.append(float(k.get("volume", k.get("turnover", 0))))
        highs.append(float(k["high"]))
        lows.append(float(k["low"]))
    return closes, vols, highs, lows

# ──────────────── ANALYSIS ──────────────
def analyze(symbol):
    try:
        closes, vols, highs, lows = fetch_klines(symbol)
        if len(closes) < 100:
            return None
        hi, lo = max(closes), min(closes)
        now = closes[-1]
        band = hi - lo
        if band <= 0:
            return None
        pos = (now - lo) / band  # 0 bottom, 1 top
        if pos < 0.05 or pos > 0.95:
            return None  # far extremes – skip
        # entry zone & direction
        if pos < 0.25:
            entry = "Long"; direction = "📈 Long"
        elif pos > 0.75:
            entry = "Short"; direction = "📉 Short"
        else:
            entry = "Neutral"; direction = "📊 Neutral"
        # simple cycles estimate
        width_pct = band / now * 100
        cycles_per_day = round(width_pct * 2, 1)
        return {
            "symbol": symbol,
            "price_range": f"${lo:,.0f} – ${hi:,.0f}",
            "volatility": f"{width_pct:.1f}%",
            "cycles": cycles_per_day,
            "entry": entry,
            "direction": direction,
        }
    except Exception as e:
        logging.warning(f"Skip {symbol}: {e}")
        return None{
            "symbol": symbol,
            "price_range": f"${lo:,.0f} – ${hi:,.0f}",
            "volatility": f"{(volatility/now)*100:.1f}%",
            "cycles": cycles_per_day,
            "entry": "\u2705 Mid‑range",
        }
    except Exception as e:
        logging.warning(f"Skip {symbol}: {e}")
        return None

# ──────────────── TELEGRAM ──────────────
def send_telegram(messages):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    for text in messages:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=10
            ).raise_for_status()
        except Exception as e:
            logging.error(f"Telegram error: {e}")

def format_alert(d):
return (
    f"*{d['symbol']}*\n"
    f"Direction: {d['recommendation']}\n"
    f"💡 Entry Zone: ✅ {d['entry_zone']}\n"
    f"Price Range: {d['price_range']}\n"
    f"Grid Count: {d['grid_count']}\n"
    f"Expected Cycles/Days: {d['cycles']}\n"
    f"Volatility: {d['volatility']}"
)

# ──────────────── MAIN LOOP ─────────────
def main():
    tickers = fetch_perp_tickers()
    sorted_perps = sorted(tickers, key=lambda t: float(t["amount"]), reverse=True)
    top10 = [t["symbol"] for t in sorted_perps if not is_excluded(t["symbol"])][:10]
    if HYPE not in top10:
        top10.append(HYPE)

    final = []
    for sym in top10:
        result = analyze(sym)
        if result:
            final.append(format_alert(result))

    if not final:
        logging.info("No valid entries found.")
        return

    # Telegram limit = 4096 chars
    buffer, messages = "", []
    for alert in final:
        if len(buffer) + len(alert) + 2 > 4000:
            messages.append(buffer)
            buffer = alert + "\n\n"
        else:
            buffer += alert + "\n\n"
    if buffer:
        messages.append(buffer)
    send_telegram(messages)

if __name__ == "__main__":
    main()
