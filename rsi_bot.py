import requests
import time
from datetime import datetime
import math

# === CONFIG ===
TELEGRAM_TOKEN = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID = "7588547693"
COINS = ["bitcoin", "ethereum", "solana"]
VS_CURRENCY = "usd"
RSI_PERIOD = 14
SAFE_RSI_MIN = 30
SAFE_RSI_MAX = 45

# === FUNCTIONS ===

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    response = requests.post(url, data=payload)
    return response.ok

def fetch_ohlc_from_coingecko(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {
        "vs_currency": VS_CURRENCY,
        "days": "2"  # returns hourly data automatically
    }
    res = requests.get(url, params=params)
    if res.status_code != 200:
        raise Exception(f"Failed to fetch data for {coin_id}: {res.text}")
    prices = res.json().get("prices", [])
    closes = [price[1] for price in prices]
    if len(closes) < RSI_PERIOD + 1:
        raise Exception(f"Not enough data to calculate RSI for {coin_id}")
    return closes


def calculate_rsi(closes, period=RSI_PERIOD):
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = closes[-i] - closes[-i - 1]
        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-change)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# === MAIN ===

def main():
    triggered = []
    for coin in COINS:
        try:
            closes = fetch_ohlc_from_coingecko(coin)
            rsi = calculate_rsi(closes)
            print(f"{coin.upper()} RSI: {rsi:.2f}")
            if SAFE_RSI_MIN < rsi < SAFE_RSI_MAX:
                triggered.append(f"{coin.upper()} RSI is {rsi:.2f} ✅")
        except Exception as e:
            error_message = f"❌ Error in RSI Bot for {coin.upper()}: {str(e)}"
            send_telegram_message(error_message)
            print(error_message)

    if triggered:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        message = f"📊 HOURLY RSI ALERT [{timestamp}]\n" + "\n".join(triggered)
        send_telegram_message(message)

if __name__ == "__main__":
    main()
