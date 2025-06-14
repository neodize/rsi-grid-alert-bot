import requests
import os
import datetime
import numpy as np

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RSI_THRESHOLD = 31
RSI_PERIOD = 14
TOP_N = 5
EXCLUDED = {"bitcoin", "ethereum", "solana", "hype"}

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def get_price_history(coin_id, vs="usd", days=2):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    try:
        r = requests.get(url, params={"vs_currency": vs, "days": days})
        r.raise_for_status()
        prices = [p[1] for p in r.json()["prices"]]
        return prices
    except Exception as e:
        print(f"[WARN] skip {coin_id.upper()}: {e}")
        return []

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram token or chat ID not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        print(f"[ERROR] Telegram send failed: {r.text}")

def format_grid_settings(prices):
    low = min(prices)
    high = max(prices)
    range_pct = (high - low) / low * 100
    grids = 15 if range_pct < 4 else 25 if range_pct < 8 else 35
    return {
        "price_range": f"${low:,.2f} – ${high:,.2f}",
        "grids": grids,
        "mode": "Arithmetic",
        "trailing": "Disabled",
        "direction": "Long"
    }

def build_grid_section(coin, settings):
    return f"""• {coin.upper()}
  • Price Range: {settings['price_range']}
  • Grids: {settings['grids']}
  • Mode: {settings['mode']}
  • Trailing: {settings['trailing']}
  • Direction: {settings['direction']}"""

def get_trending_coins():
    url = "https://api.coingecko.com/api/v3/search/trending"
    try:
        r = requests.get(url)
        r.raise_for_status()
        return [item["item"]["id"] for item in r.json()["coins"] if item["item"]["id"] not in EXCLUDED]
    except Exception as e:
        print(f"[ERROR] Trending fetch failed: {e}")
        return []

def main():
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    message = f"*HOURLY RSI ALERT — {ts}*\n"
    rsi_alerts = []
    coins = ["bitcoin", "ethereum", "solana", "hype"]
    for coin in coins:
        closes = get_price_history(coin)
        if not closes: continue
        rsi_value = rsi(closes, RSI_PERIOD)
        if rsi_value < RSI_THRESHOLD:
            rsi_alerts.append((coin.upper(), rsi_value, closes))

    for coin, rsi_val, closes in rsi_alerts:
        message += f"\n🔻 {coin} RSI {rsi_val}\n"
        grid = format_grid_settings(closes)
        message += f"📊 {coin} Grid Bot Suggestion\n"
        message += f"""• Price Range: {grid['price_range']}
• Grids: {grid['grids']}
• Mode: {grid['mode']}
• Trailing: {grid['trailing']}
• Direction: {grid['direction']}\n"""

    # Trending coins for Grid Bot
    trending = get_trending_coins()
    grid_recos = []
    for coin in trending[:TOP_N]:
        closes = get_price_history(coin)
        if not closes: continue
        settings = format_grid_settings(closes)
        grid_recos.append(build_grid_section(coin, settings))

    if grid_recos:
        message += f"\n📊 Sideways Coins to Grid Now:\n" + "\n".join(grid_recos)

    print(message)
    send_telegram(message)

if __name__ == "__main__":
    main()
