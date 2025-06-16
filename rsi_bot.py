import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.stats import linregress
from telegram import Bot

# Env config
TG_TOKEN = os.getenv("TG_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")

bot = Bot(token=TG_TOKEN)

# Parameters
INTERVAL = "60M"
LIMIT = 200
CYCLE_CUTOFF = 2.0  # in days
BREAKOUT_THRESHOLD = 0.03  # 3% range exit
MAX_CANDLES = 200
GRID_SPACING_TARGET = 0.75 / 100  # 0.75%

def fetch_symbols():
    url = "https://api.pionex.com/api/v1/market/tickers"
    response = requests.get(url)
    data = response.json()["data"]
    symbols = [i["symbol"] for i in data if i["symbol"].endswith("_USDT_PERP")]
    return symbols

def fetch_ohlcv(symbol):
    url = f"https://api.pionex.com/api/v1/market/klines?symbol={symbol}&interval={INTERVAL}&limit={LIMIT}&type=PERP"
    response = requests.get(url)
    data = response.json()
    if not isinstance(data, list) or not all(len(d) >= 5 for d in data):
        return None
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    return df

def analyze_symbol(symbol):
    df = fetch_ohlcv(symbol)
    if df is None or df.empty:
        return None

    prices = df["close"]
    high = df["high"].max()
    low = df["low"].min()
    range_pct = (high - low) / low
    last_price = prices.iloc[-1]

    # Entry Zone
    lower_bound = low + (high - low) * 0.25
    upper_bound = high - (high - low) * 0.25
    if last_price < lower_bound:
        zone = "🟢 Long"
        zone_code = "long"
    elif last_price > upper_bound:
        zone = "🔴 Short"
        zone_code = "short"
    else:
        zone = "🔁 Neutral"
        zone_code = "neutral"

    # Volatility
    volatility = np.std(prices.pct_change().dropna()) * np.sqrt(len(prices)) * 100

    # Grid Spacing
    grid_spacing = GRID_SPACING_TARGET
    grid_count = max(10, int(np.log(high / low) / np.log(1 + grid_spacing)))

    # Cycle estimation
    x = np.arange(len(prices))
    y = prices.values
    slope, intercept, r_value, p_value, std_err = linregress(x, y)
    amplitude = (high - low) / 2
    cycle = (
        np.pi * amplitude / abs(slope) / 24
        if slope != 0 else 99
    )
    cycle = round(cycle, 1)

    # Planned stop: price out of range or entry zone flipped
    planned_stop = None
    if last_price < low * (1 - BREAKOUT_THRESHOLD) or last_price > high * (1 + BREAKOUT_THRESHOLD):
        planned_stop = "🛑 Price out of range"
    elif zone_code != "long":
        planned_stop = "🛑 Exit zone flipped"

    return {
        "symbol": symbol,
        "range": f"${low:.4f} – ${high:.4f}",
        "zone": zone,
        "grids": grid_count,
        "spacing": round(grid_spacing * 100, 2),
        "volatility": round(volatility, 1),
        "cycle": cycle,
        "planned_stop": planned_stop,
    }

def send_telegram_message(message):
    try:
        bot.send_message(chat_id=TG_CHAT_ID, text=message, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        print(f"Telegram send failed: {e}")

def format_summary(results):
    messages = []
    for r in results:
        line = f"<b>{r['symbol']}</b>\n"
        line += f"📊 Range: {r['range']}\n"
        line += f"📈 Entry Zone: {r['zone']}\n"
        line += f"🧮 Grids: {r['grids']}  |  📏 Spacing: {r['spacing']}%\n"
        line += f"🌪️ Volatility: {r['volatility']}%  |  ⏱️ Cycle: {r['cycle']} days"
        if r["planned_stop"]:
            line += f"\n{r['planned_stop']}"
        messages.append(line)
    return "\n\n".join(messages)

def main():
    symbols = fetch_symbols()
    shortlisted = []
    for symbol in symbols:
        result = analyze_symbol(symbol)
        if result and result["cycle"] <= CYCLE_CUTOFF:
            shortlisted.append(result)

    if not shortlisted:
        send_telegram_message("🔍 No good Grid Trading opportunities found under cycle ≤ 2 days.")
        return

    message = format_summary(shortlisted)
    send_telegram_message("✅ <b>Grid Bot Opportunities</b> (Cycle ≤ 2d):\n\n" + message)

if __name__ == "__main__":
    main()
