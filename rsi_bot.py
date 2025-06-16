import requests
import logging
from datetime import datetime
import numpy as np

TELEGRAM_TOKEN = "<your_telegram_token>"
TELEGRAM_CHAT_ID = "<your_chat_id>"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

API_BASE = "https://api.pionex.com/api/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

EMOJI_ZONE = {
    "Long": "📈 Entry Zone: 🟢 Long",
    "Neutral": "➖ Entry Zone: ⚪️ Neutral",
    "Short": "📉 Entry Zone: 🔴 Short"
}

def get_top_symbols():
    url = f"{API_BASE}/market/tickers"
    try:
        res = requests.get(url, headers=HEADERS)
        res.raise_for_status()
        tickers = res.json().get("data", [])
        filtered = [t for t in tickers if t.get("type") == "PERP"]
        sorted_list = sorted(filtered, key=lambda x: float(x.get("baseVolume24h", 0)), reverse=True)
        return sorted_list[:10]
    except Exception as e:
        logging.error(f"Failed to get top symbols: {e}")
        return []

def fetch_klines(symbol):
    url = f"{API_BASE}/market/klines?symbol={symbol}&interval=60M&limit=200&type=PERP"
    try:
        res = requests.get(url)
        res.raise_for_status()
        klines = res.json().get("data", [])
        if not klines:
            raise RuntimeError("No klines")
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        return closes, highs, lows
    except Exception as e:
        logging.warning(f"Skip {symbol}: {e}")
        return None, None, None

def analyze(symbol):
    closes, highs, lows = fetch_klines(symbol)
    if not closes:
        return None
    
    current_price = closes[-1]
    high = max(highs[-50:])
    low = min(lows[-50:])
    range_pct = (high - low) / low * 100

    if current_price < low + (high - low) * 0.33:
        zone = "Long"
    elif current_price > high - (high - low) * 0.33:
        zone = "Short"
    else:
        zone = "Neutral"

    spacing_pct = 0.5
    grid_count = int(range_pct / spacing_pct)
    avg_grid_profit = spacing_pct - 0.12
    expected_cycle_days = round(200 / grid_count, 1)

    return {
        "symbol": symbol,
        "price": round(current_price, 6),
        "high": round(high, 6),
        "low": round(low, 6),
        "range": round(range_pct, 2),
        "zone": zone,
        "spacing": spacing_pct,
        "grids": grid_count,
        "profit": round(avg_grid_profit, 2),
        "cycle": expected_cycle_days
    }

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")

def main():
    top_symbols = get_top_symbols()
    if not top_symbols:
        logging.info("No symbols fetched")
        return

    valid = []
    for t in top_symbols:
        symbol = t["symbol"].replace("_PERP", "")
        result = analyze(symbol)
        if result:
            valid.append(result)

    if not valid:
        logging.info("No valid entries found.")
        return

    for d in valid:
        msg = (
            f"*{d['symbol']}*
"
            f"{EMOJI_ZONE[d['zone']]}
"
            f"💰 Current Price: `{d['price']}`
"
            f"📊 Range: `{d['low']} - {d['high']}` ({d['range']}%)
"
            f"🔢 Grids: `{d['grids']}` | 📈 Spacing: `{d['spacing']}%`
"
            f"💸 Est. Grid Profit (after fee): `{d['profit']}%`
"
            f"📆 Est. Cycle: `{d['cycle']} days`")

        send_telegram(msg)

if __name__ == "__main__":
    main()
