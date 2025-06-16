import requests
import logging
import numpy as np
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_top_symbols(limit=15):
    url = "https://api.pionex.com/api/v1/market/getMarket24hList"
    try:
        resp = requests.get(url).json()
        top = sorted(resp, key=lambda x: float(x["amount"]), reverse=True)
        return [t["symbol"] for t in top if t["symbol"].endswith("_USDT") and "PERP" not in t["symbol"]][:limit]
    except Exception as e:
        logging.error(f"Failed to get top symbols: {e}")
        return []

def fetch_klines(symbol):
    url = f"https://api.pionex.com/api/v1/market/klines?symbol={symbol}&interval=60M&limit=200&type=PERP"
    try:
        resp = requests.get(url).json()
        if not isinstance(resp, list):
            raise RuntimeError("No klines")
        closes = [float(x[4]) for x in resp]
        highs = [float(x[2]) for x in resp]
        lows = [float(x[3]) for x in resp]
        return closes, highs, lows
    except Exception as e:
        logging.warning(f"Skip {symbol}: {e}")
        return None, None, None

def compute_grid(closes):
    min_price = float(np.min(closes))
    max_price = float(np.max(closes))
    volatility = ((max_price - min_price) / min_price) * 100

    grid_count = 15
    spacing = round((max_price - min_price) / grid_count / min_price * 100, 2)
    zone = "Neutral"

    expected_days = "-"
    if volatility > 0:
        expected_days = round((grid_count * spacing) / volatility * 2, 1)

    return {
        "range": f"${min_price:.8f} – ${max_price:.8f}",
        "zone": zone,
        "grids": grid_count,
        "spacing": f"{spacing:.2f}%",
        "vol": f"{volatility:.2f}%",
        "days": f"{expected_days} days"
    }

def build_message(entry):
    return (f"*{entry['symbol']}*\n"
            f"📊 Range: `{entry['range']}`\n"
            f"🎯 Entry Zone: `{entry['zone']}`\n"
            f"🧮 Grids: `{entry['grids']}`  |  📏 Spacing: `{entry['spacing']}`\n"
            f"🌪️ Volatility: `{entry['vol']}`  |  ⏱️ Cycle: `{entry['days']}`")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")

def main():
    symbols = get_top_symbols()
    if not symbols:
        return

    logging.info(f"Top symbols: {symbols}")
    valid = []
    for sym in symbols:
        spot = sym.replace("_USDT", "_USDT")
        closes, highs, lows = fetch_klines(spot)
        if closes:
            grid = compute_grid(closes)
            valid.append({"symbol": sym, **grid})

    if not valid:
        logging.info("No valid entries found.")
        return

    # Load previous
    prev_file = "prev_symbols.txt"
    prev = set()
    if os.path.exists(prev_file):
        with open(prev_file) as f:
            prev = set(f.read().splitlines())

    current = set([d["symbol"] for d in valid])
    stopped = prev - current

    if stopped:
        msg = "🛑 *Stop Suggestion*:\n" + "\n".join([f"- `{s}`" for s in sorted(stopped)])
        send_telegram(msg)

    for d in valid:
        send_telegram(build_message(d))

    with open(prev_file, "w") as f:
        f.write("\n".join(sorted(current)))

if __name__ == "__main__":
    main()
