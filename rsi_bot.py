import requests, numpy as np
from datetime import datetime, timezone
import os

# === CONFIGURATION ===
VS = "usd"
COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "dogwifcoin": "WIF",
    "pepe": "PEPE"
}
RSI_LOWER, RSI_UPPER = 35, 65

# === TELEGRAM SETTINGS ===
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BOT_CHAT_ID")

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram token or chat ID not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def calc_rsi(closes, period=14):
    closes = np.array(closes)
    deltas = np.diff(closes)
    seed = deltas[:period]
    up = seed[seed > 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = 100 - 100 / (1 + rs)

    for i in range(period, len(deltas)):
        delta = deltas[i]
        upval = max(delta, 0)
        downval = -min(delta, 0)
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi = 100 - 100 / (1 + rs)
    return rsi

def get_closes(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": VS, "days": 2}
    r = requests.get(url, params=params)
    r.raise_for_status()
    return [x[1] for x in r.json()["prices"]]

def suggest_settings(rsi, prices):
    min_price = round(min(prices[-48:]), -1)
    max_price = round(max(prices[-48:]), -1)
    grid_qty = 20
    grid_mode = "Arithmetic"
    trailing = "✅ Enabled"
    if rsi < RSI_LOWER:
        direction = "Long"
    elif rsi > RSI_UPPER:
        direction = "Short"
    else:
        direction = "Neutral"
    return min_price, max_price, grid_qty, grid_mode, trailing, direction

def main():
    messages = []
    for coin_id, symbol in COINS.items():
        try:
            closes = get_closes(coin_id)
            rsi = calc_rsi(closes[-15:])
            if rsi < RSI_LOWER or rsi > RSI_UPPER:
                min_p, max_p, grids, mode, trail, direct = suggest_settings(rsi, closes)
                messages.append(f"""🔻 {symbol} RSI is {rsi:.2f} — {"Oversold" if rsi < RSI_LOWER else "Overbought"}!

📊 Suggested Grid Bot Settings ({symbol}/{VS.upper()}):
- Price Range: {min_p} – {max_p}
- Grids: {grids}
- Mode: {mode}
- Trailing: {trail}
- Direction: {direct}
""")
        except Exception as e:
            print(f"[WARN] {symbol}: {e}")

    if messages:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        full_message = f"📉 RSI Alert Bot — {ts}\n\n" + "\n".join(messages)
        send_telegram(full_message)

if __name__ == "__main__":
    main()
