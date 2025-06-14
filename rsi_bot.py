import requests

symbol = "BTCUSDT"
interval = "1h"
rsi_period = 14
safe_range = (30, 70)

telegram_token = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
chat_id = "7588547693"

def fetch_klines(symbol, interval, limit=100):
    url = f"https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    res = requests.get(url, params=params)
    return [float(c[4]) for c in res.json()]  # close prices

def compute_rsi(closes, period=14):
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(-min(delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(closes) - 1):
        delta = closes[i+1] - closes[i]
        gain = max(delta, 0)
        loss = -min(delta, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    rs = avg_gain / avg_loss if avg_loss != 0 else 100
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    data = {"chat_id": chat_id, "text": msg}
    requests.post(url, data=data)

try:
    closes = fetch_klines(symbol, interval)
    rsi = compute_rsi(closes)
    print(f"RSI: {rsi}")
    if safe_range[0] < rsi < safe_range[1]:
        send_telegram(f"✅ RSI is {rsi} for {symbol} — Safe to launch Pionex Grid Bot!")
    else:
        send_telegram(f"⚠️ RSI is {rsi} for {symbol} — NOT safe for Pionex Grid Bot.")
except Exception as e:
    send_telegram(f"❌ Error in RSI Bot: {str(e)}")
