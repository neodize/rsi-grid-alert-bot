import requests
import time
import threading
import logging
import os
from datetime import datetime
from math import isclose

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Config
PIONEX_API = "https://api.pionex.com"
SCAN_INTERVAL = 60 * 60  # scan every hour
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FORCE_DAILY_SUMMARY = os.environ.get("FORCE_DAILY_SUMMARY", "false").lower() == "true"

# --- Utilities ---

def send_telegram(message: str):
    """Send message to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram credentials missing. Message not sent.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=data, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")


def fetch_perp_symbols():
    """Get all available PERP symbols from Pionex."""
    url = f"{PIONEX_API}/api/v1/market/ticker"
    js = requests.get(url, timeout=10).json()
    symbols = [item["symbol"] for item in js.get("data", []) if item["symbol"].endswith("_PERP")]
    return symbols


def fetch_klines(symbol: str, interval="60M", limit=200):
    """Fetch historical kline data for PERP symbol."""
    url = f"{PIONEX_API}/api/v1/market/contract/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    js = requests.get(url, params=params, timeout=10).json()
    if not js.get("data") or "klines" not in js["data"]:
        raise RuntimeError(f"Kline fetch failed {symbol}: {js}")
    closes = [float(k["close"]) for k in js["data"]["klines"]]
    highs  = [float(k["high"]) for k in js["data"]["klines"]]
    lows   = [float(k["low"]) for k in js["data"]["klines"]]
    return closes, highs, lows


def is_trending_grid_ready(closes, highs, lows):
    """Detect grid-ready trending coin using volatility breakout."""
    if len(closes) < 20:
        return False
    recent_range = max(highs[-20:]) - min(lows[-20:])
    if recent_range == 0:
        return False
    change = closes[-1] - closes[-20]
    breakout_ratio = abs(change) / recent_range
    return breakout_ratio > 0.7 and not isclose(closes[-1], closes[-2], rel_tol=1e-3)


def scan_and_notify():
    """Scan market and notify if good PERP grid candidates found."""
    logging.info("Scanning PERP markets...")
    symbols = fetch_perp_symbols()
    if not symbols:
        logging.warning("No symbols found.")
        return

    results = []

    def scan_symbol(symbol):
        try:
            closes, highs, lows = fetch_klines(symbol)
            if is_trending_grid_ready(closes, highs, lows):
                logging.info(f"✅ {symbol} is grid-ready")
                results.append(symbol)
            else:
                logging.info(f"❌ {symbol} not trending/grid-ready")
        except Exception as e:
            logging.info(f"{symbol} error: {e}")

    threads = []
    for symbol in symbols:
        t = threading.Thread(target=scan_symbol, args=(symbol,))
        t.start()
        threads.append(t)
        time.sleep(0.05)  # prevent overload

    for t in threads:
        t.join()

    # Report
    if results:
        send_telegram("📈 *PERP Grid-Ready Coins Found:*\n" + "\n".join(f"• `{s}`" for s in results))
    elif FORCE_DAILY_SUMMARY:
        send_telegram("📉 No grid-ready PERP symbols this round.")


def run_loop():
    while True:
        scan_and_notify()
        logging.info(f"Waiting {SCAN_INTERVAL / 60:.0f} minutes before next scan...")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_loop()
