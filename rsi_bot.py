import requests
import numpy as np
import time
from datetime import datetime
import os
import pytz
from scipy.signal import argrelextrema
from telegram import Bot

# === CONFIGURATION ===
PIONEX_TICKERS_URL = "https://api.pionex.com/api/v1/market/tickers"
KLINES_URL = "https://api.pionex.com/api/v1/market/klines?symbol={symbol}&interval=60M&limit=200&type=PERP"
VOLUME_THRESHOLD = 5_000_000  # minimum 24h volume
MAX_CYCLE_DAYS = 2.0
SPACING_TARGET = 0.75  # % per grid
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
MYT = pytz.timezone("Asia/Kuala_Lumpur")

bot = Bot(token=TELEGRAM_TOKEN)

# === CORE FUNCTIONS ===

def fetch_symbols():
    response = requests.get(PIONEX_TICKERS_URL)
    try:
        json_data = response.json()
        if isinstance(json_data, dict) and "data" in json_data:
            data = json_data["data"]
            symbols = [i["symbol"] for i in data if isinstance(i, dict)
                       and i.get("symbol", "").endswith("_USDT_PERP")
                       and float(i.get("baseVolume", 0)) * float(i.get("last", 0)) > VOLUME_THRESHOLD]
            return symbols
        else:
            print("Unexpected format from /tickers:", json_data)
            return []
    except Exception as e:
        print(f"Error fetching symbols: {e}")
        return []

def fetch_ohlcv(symbol):
    url = KLINES_URL.format(symbol=symbol)
    response = requests.get(url)
    try:
        data = response.json()["data"]
        closes = [float(i[4]) for i in data]
        highs = [float(i[2]) for i in data]
        lows = [float(i[3]) for i in data]
        times = [int(i[0]) for i in data]
        return closes, highs, lows, times
    except:
        return [], [], [], []

def detect_range(closes):
    prices = np.array(closes)
    highs = argrelextrema(prices, np.greater, order=5)[0]
    lows = argrelextrema(prices, np.less, order=5)[0]
    if len(highs) < 1 or len(lows) < 1:
        return None, None
    return round(prices[lows].min(), 6), round(prices[highs].max(), 6)

def determine_entry_zone(price, low, high):
    if price < low or price > high:
        return "🛑 Out of Range"
    third = (high - low) / 3
    if price < low + third:
        return "🟢 Long"
    elif price > high - third:
        return "🔴 Short"
    else:
        return "🔁 Neutral"

def calculate_cycle(hours, closes):
    cycles = []
    for i in range(1, len(closes)):
        change = abs(closes[i] - closes[i-1]) / closes[i-1]
        if change > 0.03:
            cycles.append(hours[i] - hours[i-1])
    if not cycles:
        return None
    avg_cycle = np.mean(cycles) / 24  # in days
    return round(avg_cycle, 2)

def dynamic_grid_params(low, high):
    range_pct = (high - low) / low * 100
    grids = max(10, min(150, int(range_pct / SPACING_TARGET)))
    spacing = round(range_pct / grids, 2)
    return grids, spacing

def format_alert(symbol, low, high, price, zone, grids, spacing, vol, cycle):
    return f"""{symbol}
📊 Range: ${low:.4f} – ${high:.4f}
📈 Entry Zone: {zone}
🧮 Grids: {grids}  |  📏 Spacing: {spacing}%
🌪️ Volatility: {vol:.1f}%  |  ⏱️ Cycle: {cycle} days
"""

def send_telegram(message):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        print("Telegram error:", e)

# === MAIN WORKFLOW ===

def main():
    symbols = fetch_symbols()
    if not symbols:
        print("No valid symbols found.")
        return

    now = datetime.now(MYT).strftime('%Y-%m-%d %H:%M')
    summary = f"📡 Grid Opportunities @ {now} MYT\n\n"
    alerts = []
    
    for symbol in symbols:
        closes, highs, lows, times = fetch_ohlcv(symbol)
        if len(closes) < 50:
            continue

        low, high = detect_range(closes)
        if not low or not high or high - low <= 0:
            continue

        price = closes[-1]
        zone = determine_entry_zone(price, low, high)
        if zone not in ["🟢 Long", "🔴 Short"]:
            continue

        cycle = calculate_cycle([t // 3600000 for t in times], closes)
        if not cycle or cycle > MAX_CYCLE_DAYS:
            continue

        volatility = np.std(closes[-48:]) / np.mean(closes[-48:]) * 100
        grids, spacing = dynamic_grid_params(low, high)

        msg = format_alert(symbol, low, high, price, zone, grids, spacing, volatility, cycle)
        alerts.append(msg)

        # Planned stop alert
        stop_msg = f"""🛑 Planned Stop Alert for {symbol}
⏹ Exit conditions:
• Price exits range (${low:.4f} – ${high:.4f})
• Entry Zone flips from {zone} to opposite
"""
        alerts.append(stop_msg)

        time.sleep(0.5)

    if alerts:
        send_telegram(summary + "\n".join(alerts[:10]))  # Limit per Telegram limits
    else:
        send_telegram(f"No qualifying grid bots found @ {now} MYT.")

if __name__ == "__main__":
    main()
