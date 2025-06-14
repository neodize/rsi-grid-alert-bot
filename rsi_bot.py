import requests
import numpy as np
import os

# === CONFIG ===
symbol = "BTCUSDT"
interval = "1h"
rsi_period = 14
safe_rsi_min = 40
safe_rsi_max = 60

telegram_token = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
chat_id = "7588547693"

def get_candles(symbol, interval, limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json()
        candles = [float(c[4]) for c in data]  # closing prices
        return candles
    except Exception as e:
        raise Exception(f"Failed to fetch candles: {e}")

def calculate_rsi(prices, period):
    if len(prices) < period + 1:
        raise Exception(f"Not enough data to calculate RSI. Need {period+1}, got {len(prices)}")

    deltas = np.diff(prices)
    ups = deltas.clip(min=0)
    downs = -1 * deltas.clip(max=0)

    avg_gain = np.mean(ups[:period])
    avg_loss = np.mean(downs[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + ups[i]) / period
        avg_loss = (avg_loss * (period - 1) + downs[i]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg}
    requests.post(url, data=payload)

try:
    prices = get_candles(symbol, interval)

    if len(prices) < rsi_period + 1:
        raise Exception(f"Not enough candles to calculate RSI (got {len(prices)})")

    rsi = calculate_rsi(prices, rsi_period)
    rsi_msg = f"🟢 {symbol} RSI Alert\nRSI ({interval}): {rsi:.2f}"

    if safe_rsi_min <= rsi <= safe_rsi_max:
        rsi_msg += "\n✅ RSI is in safe zone. Consider launching grid bot."
    else:
        rsi_msg += "\n❌ RSI not in ideal range. Wait."

    send_telegram(rsi_msg)

except Exception as e:
    send_telegram(f"❌ Error in RSI Bot: {str(e)}")
