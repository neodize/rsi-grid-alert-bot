import requests
import time
from datetime import datetime, timezone
import os
import re

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')  # Use env var or fallback
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')  # Use env var or fallback
COINGECKO_API = 'https://api.coingecko.com/api/v3'
TOP_COINS_LIMIT = 50  # Number of top coins by market cap to scan
MIN_VOLUME = 10_000_000  # Minimum daily trading volume in USD
MIN_PRICE = 0.01  # Minimum price to filter out micro-cap tokens
TOP_COINS_TO_EXCLUDE = 20  # Exclude top 20 coins to focus on smaller tokens
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']  # Prioritized tokens
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']  # Possible ID variants for HYPE

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        print(f"Telegram sent successfully: {message[:50]}...")
    except requests.exceptions.RequestException as e:
        print(f"Telegram send failed: {e}, status: {getattr(e.response, 'status_code', 'N/A')}")
        time.sleep(60)  # Retry after 1 minute
        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            print(f"Telegram retry succeeded: {message[:50]}...")
        except requests.exceptions.RequestException as e2:
            print(f"Telegram retry failed: {e2}")

def fetch_market_data():
    print("Fetching market data from CoinGecko...")
    url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={TOP_COINS_LIMIT}&page=1&sparkline=true"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = [coin for coin in response.json() if coin['total_volume'] > MIN_VOLUME and coin['current_price'] > MIN_PRICE]
    print(f"Filtered data count: {len(data)}")
    # Exclude table coins (e.g., BTC3L, ETH3S)
    filtered_data = [coin for coin in data if not re.search(r'(\d+[LS])$', coin['symbol'].upper())]
    print(f"After table coin filter: {len(filtered_data)}")
    # Exclude top 20 coins but ensure main tokens are included
    smaller_tokens = [coin for coin in filtered_data if filtered_data.index(coin) >= TOP_COINS_TO_EXCLUDE]
    print(f"After top 20 exclusion: {len(smaller_tokens)}")
    # Add main tokens if not already in the list
    for token_id in MAIN_TOKENS:
        if not any(coin['id'] == token_id for coin in smaller_tokens):
            main_coin = next((coin for coin in data if coin['id'] == token_id), None)
            if main_coin:
                print(f"Adding main token from initial data: {token_id}")
                smaller_tokens.append(main_coin)
            else:
                # Handle HYPE specifically with variants and retries
                if token_id == 'hyperliquid':
                    for variant in HYPE_VARIANTS:
                        for attempt in range(3):  # Retry up to 3 times
                            try:
                                direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={variant}&sparkline=true"
                                direct_response = requests.get(direct_url, timeout=10)
                                direct_response.raise_for_status()
                                direct_data = direct_response.json()
                                if direct_data and direct_data[0]['total_volume'] > MIN_VOLUME and direct_data[0]['current_price'] > MIN_PRICE:
                                    print(f"Direct fetch success for {variant}")
                                    smaller_tokens.append(direct_data[0])
                                    break
                            except requests.exceptions.RequestException as e:
                                print(f"Fetch attempt {attempt + 1} for {variant} failed: {e}")
                                time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                                if attempt == 2:
                                    print(f"Failed to fetch {variant} after 3 attempts")
                else:
                    # Direct fetch for other main tokens
                    direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={token_id}&sparkline=true"
                    direct_response = requests.get(direct_url, timeout=10)
                    direct_response.raise_for_status()
                    direct_data = direct_response.json()
                    if direct_data and direct_data[0]['total_volume'] > MIN_VOLUME and direct_data[0]['current_price'] > MIN_PRICE:
                        print(f"Direct fetch success for {token_id}")
                        smaller_tokens.append(direct_data[0])
    print(f"Final market data count: {len(smaller_tokens)}")
    return smaller_tokens

def calc_rsi(prices):
    if len(prices) < 15:  # Need at least 15 points for 14-period RSI
