import requests, numpy as np
from datetime import datetime, timezone
from rsi_bot_helpers import calc_rsi, send_telegram

VS = "usd"
COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "dogwifcoin": "WIF",
    "pepe": "PEPE"
}
RSI_LOWER, RSI_UPPER = 35, 65

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
