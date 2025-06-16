# Enhanced Grid Scanner v5.0.4 - Small Cap Discovery Edition
# Description: Scans top 100 volume PERP symbols on Pionex, detects coins with strong Long/Short entry zones and filters for trending small-cap opportunities.

import requests
import math
import time
import logging
from datetime import datetime
from telegram import Bot

# === CONFIG ===
TOP_N = 100
INTERVAL = "60M"
LIMIT = 200
VOLATILITY_THRESHOLD = 5
SPACING = 0.75
CHANGE_7D_THRESHOLD = 10
TELEGRAM_TOKEN = "<YOUR_SECRET_BOT_TOKEN>"
TELEGRAM_CHAT_ID = "<YOUR_SECRET_CHAT_ID>"
PIONEX_KLINE_ENDPOINT = "https://api.pionex.com/api/v1/market/klines"
PIONEX_TICKER_ENDPOINT = "https://api.pionex.com/api/v1/market/tickers"

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

bot = Bot(token=TELEGRAM_TOKEN)

# === HELPERS ===
def fetch_symbols():
    r = requests.get(PIONEX_TICKER_ENDPOINT)
    data = r.json()
    symbols = [i['symbol'] for i in data if i['symbol'].endswith('_USDT_PERP')]
    sorted_data = sorted(data, key=lambda x: -float(x['quoteVolume']))[:TOP_N]
    return [i['symbol'] for i in sorted_data if i['symbol'] in symbols]

def fetch_klines(symbol):
    url = f"{PIONEX_KLINE_ENDPOINT}?symbol={symbol}&interval={INTERVAL}&limit={LIMIT}&type=PERP"
    r = requests.get(url)
    if r.status_code != 200:
        return []
    return r.json().get("data", [])

def analyze_symbol(symbol):
    klines = fetch_klines(symbol)
    if not klines or len(klines) < 50:
        logging.warning(f"Skip {symbol}: no klines")
        return None

    prices = [float(k[4]) for k in klines]  # closing prices
    high = max(prices)
    low = min(prices)
    now = prices[-1]
    range_pct = ((high - low) / low) * 100
    change_7d = ((now - prices[0]) / prices[0]) * 100

    if change_7d < CHANGE_7D_THRESHOLD:
        return None

    zone = "🟢 Long" if now < low + 0.25 * (high - low) else "🔴 Short" if now > low + 0.75 * (high - low) else None
    if not zone:
        return None

    grids = max(10, min(100, round(range_pct / SPACING)))
    spacing = SPACING
    cycle = round((grids / (range_pct + 1e-6)) * 1.5, 1)

    return {
        "symbol": symbol,
        "low": low,
        "high": high,
        "now": now,
        "zone": zone,
        "grids": grids,
        "spacing": spacing,
        "volatility": round(range_pct, 1),
        "cycle": cycle
    }

def format_result(d):
    fmt = lambda p: f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"
    return (
        f"{d['symbol']}\n"
        f"📊 Range: {fmt(d['low'])} – {fmt(d['high'])}\n"
        f"📈 Entry Zone: {d['zone']}\n"
        f"🧮 Grids: {d['grids']}  |  📏 Spacing: {d['spacing']}%\n"
        f"🌪️ Volatility: {d['volatility']}%  |  ⏱️ Cycle: {d['cycle']} days"
    )

def main():
    symbols = fetch_symbols()
    logging.info(f"Scanning {len(symbols)} symbols...")
    msgs = []

    for s in symbols:
        result = analyze_symbol(s)
        if result:
            msgs.append(result)

    if not msgs:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="No trending Long/Short grid setups found today.")
        return

    msg_text = "\n\n".join([format_result(m) for m in msgs])
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg_text)

if __name__ == '__main__':
    main()
