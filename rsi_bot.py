import os
import requests
import logging
import time
from telegram import Bot

PIONEX_API = "https://api.pionex.com"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO)

def fetch_klines(symbol: str, interval: str = "1h", limit: int = 200):
    # Fix symbol for Pionex kline API
    if symbol.endswith("_PERP"):
        symbol = symbol.replace("_PERP", "_USDT")

    url = f"{PIONEX_API}/api/v1/market/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    js = r.json()

    if js.get("code", 0) != 0 or "data" not in js or "klines" not in js["data"]:
        raise ValueError(f"Kline fetch failed for {symbol}: {js}")

    closes, highs, lows = [], [], []
    for k in js["data"]["klines"]:
        closes.append(float(k["close"]))
        highs.append(float(k["high"]))
        lows.append(float(k["low"]))
    return closes, highs, lows

def get_coin_list():
    r = requests.get(f"{PIONEX_API}/api/v1/exchangeInfo", timeout=10)
    data = r.json()
    return [
        {
            "symbol": x["symbol"],
            "symbol_raw": x["symbol"],
            "base": x["baseAsset"],
            "quote": x["quoteAsset"],
        }
        for x in data["symbols"]
        if x["symbol"].endswith("_PERP")
    ]

def analyze_coin(info):
    try:
        closes, highs, lows = fetch_klines(info["symbol_raw"], "1h", 200)
        low = min(lows)
        high = max(highs)
        price = closes[-1]
        width = high - low
        within_range = low <= price <= high
        cycles_per_day = sum(
            1 for i in range(1, len(closes)) if abs(closes[i] - closes[i-1]) > width / 20
        )

        return {
            "symbol": info["symbol_raw"],
            "low": round(low, 3),
            "high": round(high, 3),
            "price": round(price, 3),
            "within_range": within_range,
            "cycles": cycles_per_day,
            "score": cycles_per_day * (1 if within_range else 0.5),
        }

    except Exception as e:
        logging.warning(f"Analysis failed for {info['symbol_raw']}: {e}")
        return None

def format_report(coin):
    trend = "✅ Neutral" if coin["within_range"] else "⚠️ Trending"
    return (
        f"📊 *{coin['symbol']}*\n"
        f"Price: `{coin['price']}`\n"
        f"Range: `{coin['low']} - {coin['high']}`\n"
        f"Trend: {trend}\n"
        f"Cycles/day: `{coin['cycles']}`\n"
        f"Leverage: `10X Futures`\n"
        f"_Ideal for Grid Bot Setup_\n"
    )

def send_telegram_chunks(messages, chunk_size=3):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    for i in range(0, len(messages), chunk_size):
        text = "\n\n".join(messages[i:i + chunk_size])
        try:
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Telegram send failed: {e}")
            time.sleep(3)

def main():
    logging.info("Running Grid Scanner...")
    coins = get_coin_list()
    results = []
    for info in coins:
        result = analyze_coin(info)
        if result:
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:10]  # limit to top 7–10
    messages = [format_report(c) for c in top]

    send_telegram_chunks(messages, chunk_size=3)
    logging.info("Grid Scanner complete.")

if __name__ == "__main__":
    main()
