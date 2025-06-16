import requests
import math
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = 'your_bot_token'
TELEGRAM_CHAT_ID = 'your_chat_id'

TOP_COINS = [
    'bitcoin', 'ethereum', 'bnb', 'solana', 'arbitrum', 'pepe',
    'dogecoin', 'shiba-inu', 'aptos', 'mantle', 'near', 'starknet',
    'sei', 'sui', 'ton', 'injective', 'render', 'coredaoorg', 'ethena', 'bnb',
    'lido-dao', 'staked-ether', 'ethereum-name-service', 'osmosis'
]

MAX_COINS = 12
GECKO_API = 'https://api.coingecko.com/api/v3/coins/markets'
HEADERS = {'accept': 'application/json'}

# Filters
MIN_VOL_24H = 5_000_000
MIN_SPARK_POINTS = 20
MAX_SPREAD = 0.35

# Entry filter: price must be between 30-60% of the sparkline range
ENTRY_ZONE_LOWER = 0.30
ENTRY_ZONE_UPPER = 0.60

# Optional: Stop alert if price exits grid band by ±2%
ABORT_THRESHOLD = 0.02


def fetch_top_perp_coins():
    params = {
        'vs_currency': 'usd',
        'order': 'volume_desc',
        'per_page': 100,
        'page': 1,
        'sparkline': 'true',
        'price_change_percentage': '24h'
    }
    r = requests.get(GECKO_API, headers=HEADERS, params=params)
    return r.json()


def analyze_coin(coin):
    sparkline = coin.get('sparkline_in_7d', {}).get('price', [])
    if len(sparkline) < MIN_SPARK_POINTS:
        return None

    high = max(sparkline)
    low = min(sparkline)
    spread = (high - low) / low
    now = coin['current_price']

    if coin['total_volume'] < MIN_VOL_24H or spread > MAX_SPREAD:
        return None

    # Entry zone check
    position = (now - low) / (high - low)
    in_entry_range = ENTRY_ZONE_LOWER <= position <= ENTRY_ZONE_UPPER
    entry_tag = "✅ Mid-range" if in_entry_range else "❌ Near edge"

    return {
        'symbol': coin['symbol'].upper(),
        'name': coin['name'],
        'price': now,
        'low': low,
        'high': high,
        'spread': spread,
        'position': position,
        'entry_tag': entry_tag
    }


def format_alert(data):
    low = round(data['low'], 3)
    high = round(data['high'], 3)
    price = round(data['price'], 3)
    return f"""
📊 *Grid Opportunity Alert*

*{data['name']} ({data['symbol']})*
Current Price: `${price}`
Range: `${low}` - `${high}`
💡 Entry Zone: {data['entry_tag']}
Leverage: 5–15× Perpetual

[CoinGecko](https://www.coingecko.com/en/coins/{data['name'].lower().replace(' ', '-')})
"""


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown'
    }
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Telegram send failed: {e}")


def scan():
    print("Scanning...")
    coins = fetch_top_perp_coins()
    hits = []

    for coin in coins:
        if coin['id'] not in TOP_COINS:
            continue

        data = analyze_coin(coin)
        if data:
            hits.append(data)

        if len(hits) >= MAX_COINS:
            break

    for hit in hits:
        send_telegram_message(format_alert(hit))


def abort_if_out_of_range(current_price, low, high):
    threshold_low = low * (1 - ABORT_THRESHOLD)
    threshold_high = high * (1 + ABORT_THRESHOLD)
    if current_price < threshold_low or current_price > threshold_high:
        send_telegram_message(
            f"🔴 *EXIT ALERT*\nPrice {round(current_price,3)} out of band {round(low,3)} – {round(high,3)}"
        )


if __name__ == '__main__':
    scan()
