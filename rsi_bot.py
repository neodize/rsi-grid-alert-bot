import requests
import numpy as np
import os

# === CONFIG ===
BINANCE_SYMBOL = "BTCUSDT"
INTERVAL = "1h"
RSI_LOWER = 35
RSI_UPPER = 65
LIMIT = 100

TELEGRAM_TOKEN = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID = "7588547693"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        res = requests.post(url, json=payload)
        if not res.ok:
            print(f"❌ Failed to send Telegram message: {res.text}")
    except Exception as e:
        print(f"❌ Telegram send error: {str(e)}")

def fetch_rsi(symbol=BINANCE_SYMBOL, interval=INTERVAL, limit=LIMIT):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        candles = response.json()

        if not isinstance(candles, list) or len(candles) == 0:
            raise ValueError(f"Empty or invalid response: {candles}")

        closes = [float(c[4]) for c in candles]
        if len(closes) < 15:
            raise ValueError(f"Not enough closes for RSI: got {len(closes)}")

        deltas = np.diff(closes)
        ups = deltas.clip(min=0)
        downs = -1 * deltas.clip(max=0)
        avg_gain = np.mean(ups[:14])
        avg_loss = np.mean(downs[:14])

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)
    except Exception as e:
        raise ValueError(f"Failed to fetch candles: {str(e)}")

def main():
    try:
        rsi = fetch_rsi()
        print(f"✅ RSI: {rsi}")
        if RSI_LOWER <= rsi <= RSI_UPPER:
            send_telegram(f"📈 RSI is {rsi} — Safe range for Grid Bot!")
        else:
            print(f"ℹ️ RSI {rsi} outside range {RSI_LOWER}-{RSI_UPPER}")
    except Exception as e:
        send_telegram(f"❌ Error in RSI Bot: {str(e)}")

if __name__ == "__main__":
    main()
