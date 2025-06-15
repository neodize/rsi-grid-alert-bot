import requests
import time
from datetime import datetime, timezone
import os
import re
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')
COINGECKO_API = 'https://api.coingecko.com/api/v3'
TOP_COINS_LIMIT = 50
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
TOP_COINS_TO_EXCLUDE = 20
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']

def send_telegram(message):
    token_source = "GitHub Secrets" if os.getenv('TELEGRAM_TOKEN') else "fallback"
    chat_id_source = "GitHub Secrets" if os.getenv('TELEGRAM_CHAT_ID') else "fallback"
    logging.info(f"Attempting to send Telegram message using token from {token_source} and chat_id from {chat_id_source}")
    logging.info(f"Token (partial): {TELEGRAM_TOKEN[:10]}..., Chat ID: {TELEGRAM_CHAT_ID}")

    if not TELEGRAM_TOKEN.strip() or not TELEGRAM_CHAT_ID.strip():
        logging.warning(f"TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is empty or unset, skipping message: {message[:50]}...")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Telegram sent successfully: {message[:50]}...")
    except requests.exceptions.RequestException as e:
        error_details = f"Telegram send failed: {e}, status: {getattr(e.response, 'status_code', 'N/A')}"
        if hasattr(e.response, 'text'):
            error_details += f", response: {e.response.text}"
        error_details += f", token (partial): {TELEGRAM_TOKEN[:10]}..., chat_id: {TELEGRAM_CHAT_ID}"
        logging.error(error_details)
        time.sleep(60)
        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            logging.info(f"Telegram retry succeeded: {message[:50]}...")
        except requests.exceptions.RequestException as e2:
            error_details = f"Telegram retry failed: {e2}, status: {getattr(e2.response, 'status_code', 'N/A')}"
            if hasattr(e2.response, 'text'):
                error_details += f", response: {e2.response.text}"
            error_details += f", token (partial): {TELEGRAM_TOKEN[:10]}..., chat_id: {TELEGRAM_CHAT_ID}"
            logging.error(error_details)
            logging.warning(f"Skipping Telegram message due to persistent failure: {message[:50]}...")
            return

def fetch_market_data():
    logging.info("Fetching market data from CoinGecko...")
    url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={TOP_COINS_LIMIT}&page=1&sparkline=true"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = [coin for coin in response.json() if coin['total_volume'] > MIN_VOLUME and coin['current_price'] > MIN_PRICE]
    logging.info(f"Filtered data count: {len(data)}")
    filtered_data = [coin for coin in data if not re.search(r'(\d+[LS])$', coin['symbol'].upper())]
    logging.info(f"After table coin filter: {len(filtered_data)}")
    smaller_tokens = [coin for coin in filtered_data if filtered_data.index(coin) >= TOP_COINS_TO_EXCLUDE]
    logging.info(f"After top 20 exclusion: {len(smaller_tokens)}")
    
    # Debug: Log existing tokens
    existing_ids = [coin['id'] for coin in smaller_tokens]
    logging.info(f"Existing token IDs: {existing_ids}")
    
    for token_id in MAIN_TOKENS:
        if not any(coin['id'] == token_id for coin in smaller_tokens):
            main_coin = next((coin for coin in data if coin['id'] == token_id), None)
            if main_coin:
                logging.info(f"Adding main token from initial data: {token_id}")
                smaller_tokens.append(main_coin)
            else:
                if token_id == 'hyperliquid':
                    for variant in HYPE_VARIANTS:
                        for attempt in range(3):
                            try:
                                direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={variant}&sparkline=true"
                                direct_response = requests.get(direct_url, timeout=10)
                                direct_response.raise_for_status()
                                direct_data = direct_response.json()
                                if direct_data and direct_data[0]['total_volume'] > MIN_VOLUME and direct_data[0]['current_price'] > MIN_PRICE:
                                    logging.info(f"Direct fetch success for {variant}")
                                    # Debug: Log the fetched coin data
                                    coin_data = direct_data[0]
                                    logging.info(f"Fetched coin ID: {coin_data['id']}, Symbol: {coin_data['symbol']}")
                                    smaller_tokens.append(coin_data)
                                    break
                            except requests.exceptions.RequestException as e:
                                logging.error(f"Fetch attempt {attempt + 1} for {variant} failed: {e}")
                                time.sleep(2 ** attempt)
                                if attempt == 2:
                                    logging.error(f"Failed to fetch {variant} after 3 attempts")
                else:
                    direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={token_id}&sparkline=true"
                    try:
                        direct_response = requests.get(direct_url, timeout=10)
                        direct_response.raise_for_status()
                        direct_data = direct_response.json()
                        if direct_data and direct_data[0]['total_volume'] > MIN_VOLUME and direct_data[0]['current_price'] > MIN_PRICE:
                            logging.info(f"Direct fetch success for {token_id}")
                            smaller_tokens.append(direct_data[0])
                    except requests.exceptions.RequestException as e:
                        logging.error(f"Direct fetch failed for {token_id}: {e}")
    
    # Debug: Log final token IDs
    final_ids = [coin['id'] for coin in smaller_tokens]
    logging.info(f"Final token IDs: {final_ids}")
    logging.info(f"Final market data count: {len(smaller_tokens)}")
    return smaller_tokens

def calc_rsi(prices):
    if len(prices) < 15:
        return None
    gains = []
    losses = []
    for i in range(1, 15):
        delta = prices[-i] - prices[-(i + 1)]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def format_price(value):
    if value >= 100:
        return f"${value:.2f}"
    elif value >= 1:
        return f"${value:.4f}"
    elif value >= 0.01:
        return f"${value:.6f}"
    else:
        return f"${value:.10f}"

def get_grid_setup(price, sparkline):
    min_price = min(sparkline) * 0.95
    max_price = max(sparkline) * 1.05
    interval = price * 0.005
    grids = max(10, min(500, round((max_price - min_price) / interval)))
    return min_price, max_price, grids

def main():
    try:
        logging.info("Starting scan...")
        market_data = fetch_market_data()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        main_alerts = []
        small_alerts = []

        if not market_data:
            logging.info("No market data available, sending empty alert")
            send_telegram(f"*HOURLY GRID TRADING ALERT — {ts}*\nNo suitable grid trading opportunities this hour.")
            return

        for coin in market_data:
            id_ = coin['id']
            current_price = coin['current_price']
            symbol = coin['symbol'].upper()
            sparkline = coin['sparkline_in_7d']['price'][-15:]
            rsi = calc_rsi(sparkline)

            # Debug: Log token processing
            logging.info(f"Processing token: ID={id_}, Symbol={symbol}")
            logging.info(f"Is {id_} in MAIN_TOKENS? {id_ in MAIN_TOKENS}")

            if rsi is None:
                continue

            grid_low, grid_high, grids = get_grid_setup(current_price, sparkline)
            price_fmt = format_price(current_price)
            low_fmt = format_price(grid_low)
            high_fmt = format_price(grid_high)

            suggestion = f"\n📊 {symbol} Grid Bot Suggestion\n• Price Range: {low_fmt} – {high_fmt}\n• Grids: {grids}\n• Mode: Arithmetic\n• Trailing: Disabled\n• Direction: "
            reason = ""

            if rsi <= 35:
                suggestion += "Long"
                reason = f"Oversold with RSI {rsi:.2f}, suggesting potential rebound."
            elif rsi >= 65:
                suggestion += "Short"
                reason = f"Overbought with RSI {rsi:.2f}, suggesting potential decline."
            else:
                suggestion += "Neutral"
                reason = f"Neutral with RSI {rsi:.2f}, indicating a ranging market."

            alert = f"{'🔻' if rsi <= 35 else '🔺' if rsi >= 65 else '📈'} {symbol} RSI {rsi:.2f}{suggestion}\nReason: {reason}"
            
            # Debug: Log alert categorization
            if id_ in MAIN_TOKENS:
                logging.info(f"Adding {symbol} to main_alerts")
                main_alerts.append(alert)
            else:
                logging.info(f"Adding {symbol} to small_alerts")
                small_alerts.append(alert)

        # Debug: Log final alert counts
        logging.info(f"Main alerts count: {len(main_alerts)}")
        logging.info(f"Small alerts count: {len(small_alerts)}")

        message = f"*HOURLY GRID TRADING ALERT — {ts}*\n"
        if main_alerts:
            message += "*Main Tokens*\n" + '\n\n'.join(main_alerts[:3]) + '\n'
        if small_alerts:
            message += "====\n*Smaller Tokens*\n" + '\n\n'.join(small_alerts[:3]) if small_alerts else ''
        if not main_alerts and not small_alerts:
            message += '\nNo suitable grid trading opportunities this hour.'

        logging.info(f"Sending Telegram message: {message[:50]}...")
        send_telegram(message)
        logging.info("Scan completed")

    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        send_telegram(f"API Error: {e}")
    except Exception as e:
        logging.error(f"Unexpected Error: {e}")
        send_telegram(f"Unexpected Error: {e}")

if __name__ == "__main__":
    main()
