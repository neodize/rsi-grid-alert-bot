import requests
import numpy as np
import time

# === CONFIGURATION ===
TELEGRAM_TOKEN = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID = "7588547693"

COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "hyperliquid": "HYPE"  # CoinGecko ID for Hype is "hyperliquid"
}

VS_CURRENCY = "usdt"
RSI_PERIOD = 14
RSI_LOWER = 35
RSI_UPPER = 70


# === FUNCTIONS ===

def fetch_ohlc_from_coingecko(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {
        "vs_currency": VS_CURRENCY,
        "days": "2"  # Automatically gives hourly candles (48+)
        # Do NOT include 'interval' param
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
    closes = np.array(closes)
    deltas = np.diff(closes)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = [100 - (100 / (1 + rs))]

    for delta in deltas[period:]:
        upval = max(delta, 0)
        downval = -min(delta, 0)
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi.append(100 - (100 / (1 + rs)))

    return rsi[-1]


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, data=payload)
    return response.status_code == 200


# === MAIN LOOP ===

try:
    alert_messages = []

    for coin_id, symbol in COINS.items():
        try:
            closes = fetch_ohlc_from_coingecko(coin_id)
            rsi = calculate_rsi(closes)

            if rsi < RSI_LOWER:
                alert_messages.append(f"🔻 *{symbol}* RSI is *{rsi:.2f}* — Oversold!")
            elif rsi > RSI_UPPER:
                alert_messages.append(f"🚀 *{symbol}* RSI is *{rsi:.2f}* — Overbought!")

        except Exception as e:
            alert_messages.append(f"❌ Error in RSI Bot for {symbol}: {e}")

    if alert_messages:
        send_telegram_message("\n".join(alert_messages))
    else:
        send_telegram_message("✅ No RSI alerts this hour.")

except Exception as e:
    send_telegram_message(f"❌ Error in RSI Bot: {e}")
