import requests
import time
from datetime import datetime, timezone
import os
import re
import logging
import math

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')
COINGECKO_API = 'https://api.coingecko.com/api/v3'
PIONEX_API = 'https://api.pionex.com/api/v1'
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']

# Cache for Pionex supported tokens
PIONEX_SUPPORTED_TOKENS = set()

def get_pionex_supported_tokens():
    """
    Fetch all supported trading pairs from Pionex API and extract base currencies
    """
    global PIONEX_SUPPORTED_TOKENS
    
    if PIONEX_SUPPORTED_TOKENS:  # Return cached data if available
        return PIONEX_SUPPORTED_TOKENS
    
    try:
        logging.info("Fetching supported tokens from Pionex API...")
        url = f"{PIONEX_API}/common/symbols"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if 'data' not in data or 'symbols' not in data['data']:
            logging.error("Unexpected Pionex API response format")
            return set()
        
        supported_tokens = set()
        spot_pairs = 0
        perp_pairs = 0
        
        for symbol_info in data['data']['symbols']:
            # Only consider enabled trading pairs
            if not symbol_info.get('enable', False):
                continue
                
            symbol_type = symbol_info.get('type', '')
            base_currency = symbol_info.get('baseCurrency', '').upper()
            quote_currency = symbol_info.get('quoteCurrency', '').upper()
            
            # Focus on PERP pairs with USDT as quote currency for futures grid trading
            if symbol_type == 'PERP' and quote_currency == 'USDT' and base_currency:
                supported_tokens.add(base_currency)
                perp_pairs += 1
            elif symbol_type == 'SPOT':
                spot_pairs += 1
        
        PIONEX_SUPPORTED_TOKENS = supported_tokens
        logging.info(f"Pionex supports {len(supported_tokens)} PERP tokens with USDT pairs")
        logging.info(f"Found {perp_pairs} PERP pairs and {spot_pairs} SPOT pairs")
        logging.info(f"Sample supported PERP tokens: {list(supported_tokens)[:10]}")
        
        return supported_tokens
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch Pionex supported tokens: {e}")
        return set()
    except Exception as e:
        logging.error(f"Error processing Pionex API response: {e}")
        return set()

def map_coingecko_to_pionex_symbol(coingecko_symbol):
    """
    Map CoinGecko symbol to Pionex symbol format
    Some tokens might have different symbols between platforms
    """
    # Common mappings
    symbol_mappings = {
        'WBTC': 'BTC',  # Wrapped Bitcoin might be listed as BTC on Pionex
        'WETH': 'ETH',  # Wrapped Ethereum might be listed as ETH on Pionex
        # Add more mappings as needed
    }
    
    return symbol_mappings.get(coingecko_symbol.upper(), coingecko_symbol.upper())

def is_token_supported_on_pionex(coingecko_symbol, coingecko_id):
    """
    Check if a token from CoinGecko is supported on Pionex
    """
    supported_tokens = get_pionex_supported_tokens()
    if not supported_tokens:
        logging.warning("No Pionex supported tokens available, allowing all tokens")
        return True
    
    # Map the symbol to Pionex format
    pionex_symbol = map_coingecko_to_pionex_symbol(coingecko_symbol)
    
    # Check if the token is supported
    is_supported = pionex_symbol in supported_tokens
    
    if not is_supported:
        logging.debug(f"Token {coingecko_symbol} ({coingecko_id}) not supported on Pionex")
    
    return is_supported

def send_telegram(message):
    token_source = "GitHub Secrets" if os.getenv('TELEGRAM_TOKEN') else "fallback"
    chat_id_source = "GitHub Secrets" if os.getenv('TELEGRAM_CHAT_ID') else "fallback"
    logging.info(f"Attempting to send Telegram message using token from {token_source} and chat_id from {chat_id_source}")
    logging.info(f"Token (partial): {TELEGRAM_TOKEN[:10]}..., Chat ID: {TELEGRAM_CHAT_ID}")

    if not TELEGRAM_TOKEN.strip() or not TELEGRAM_CHAT_ID.strip():
        logging.warning(f"TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is empty or unset, skipping message: {message[:50]}...")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message}
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
    url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=true"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    
    # Get Pionex supported tokens first
    get_pionex_supported_tokens()  # This will populate the cache
    
    # Initial filtering by volume, price, and Pionex support
    data = []
    pionex_filtered_count = 0
    volume_filtered_count = 0
    
    for coin in response.json():
        # Check volume and price first
        if coin['total_volume'] <= MIN_VOLUME or coin['current_price'] <= MIN_PRICE:
            volume_filtered_count += 1
            continue
            
        # Check if token is supported on Pionex
        if not is_token_supported_on_pionex(coin['symbol'], coin['id']):
            pionex_filtered_count += 1
            continue
            
        data.append(coin)
    
    logging.info(f"Filtering results:")
    logging.info(f"  - Volume/price filtered: {volume_filtered_count}")
    logging.info(f"  - Not supported on Pionex: {pionex_filtered_count}")
    logging.info(f"  - Remaining tokens: {len(data)}")
    
    # Separate main tokens from smaller tokens
    main_tokens_found = []
    smaller_tokens = []
    
    # Find main tokens in the filtered data
    for coin in data:
        if coin['id'] in MAIN_TOKENS:
            main_tokens_found.append(coin)
            logging.info(f"Found main token supported on Pionex PERP: {coin['symbol']} ({coin['id']})")
    
    # Get smaller tokens (excluding top 20 by market cap)
    sorted_data = sorted(data, key=lambda x: x['market_cap'], reverse=True)
    for i, coin in enumerate(sorted_data):
        if i >= 20 and coin['id'] not in MAIN_TOKENS:  # Skip top 20 by market cap
            smaller_tokens.append(coin)
    
    logging.info(f"Main tokens found on Pionex PERP: {len(main_tokens_found)}")
    logging.info(f"Smaller tokens after top 20 exclusion: {len(smaller_tokens)}")
    
    # Try to fetch missing main tokens directly if they're supported on Pionex
    missing_main_tokens = [token_id for token_id in MAIN_TOKENS 
                          if not any(coin['id'] == token_id for coin in main_tokens_found)]
    
    for token_id in missing_main_tokens:
        logging.info(f"Attempting to fetch missing main token: {token_id}")
        
        # Direct fetch for main tokens (including hyperliquid)
        for attempt in range(3):
            try:
                direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={token_id}&sparkline=true"
                direct_response = requests.get(direct_url, timeout=10)
                direct_response.raise_for_status()
                direct_data = direct_response.json()
                
                if (direct_data and len(direct_data) > 0 and 
                    direct_data[0]['total_volume'] > MIN_VOLUME and 
                    direct_data[0]['current_price'] > MIN_PRICE):
                    
                    # Check if this token is supported on Pionex
                    if is_token_supported_on_pionex(direct_data[0]['symbol'], direct_data[0]['id']):
                        logging.info(f"Successfully fetched {token_id} (Pionex PERP supported): {direct_data[0]['symbol']}")
                        main_tokens_found.append(direct_data[0])
                        break
                    else:
                        logging.info(f"Fetched {token_id} but not supported on Pionex PERP")
            except requests.exceptions.RequestException as e:
                logging.error(f"Direct fetch attempt {attempt + 1} for {token_id} failed: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    logging.error(f"Failed to fetch {token_id} after 3 attempts")
    
    # Combine main tokens and smaller tokens
    final_tokens = main_tokens_found + smaller_tokens
    
    logging.info(f"Final Pionex PERP-compatible token breakdown:")
    logging.info(f"  Main tokens: {len(main_tokens_found)} - {[coin['symbol'] for coin in main_tokens_found]}")
    logging.info(f"  Smaller tokens: {len(smaller_tokens)}")
    logging.info(f"  Total tokens: {len(final_tokens)}")
    
    return final_tokens

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

def calculate_volatility(prices):
    """Calculate rolling volatility from price array"""
    if len(prices) < 5:
        return 0.05  # Default low volatility
    
    returns = []
    for i in range(1, len(prices)):
        if prices[i-1] > 0:
            returns.append(abs(prices[i] / prices[i-1] - 1))
    
    return sum(returns) / len(returns) if returns else 0.05

def get_market_cap_tier(market_cap):
    """Classify market cap into tiers"""
    if market_cap >= 50_000_000_000:
        return "mega"      # $50B+
    elif market_cap >= 10_000_000_000:
        return "large"     # $10B-50B
    elif market_cap >= 1_000_000_000:
        return "mid"       # $1B-10B
    else:
        return "small"     # <$1B

def format_price(value):
    if value >= 100:
        return f"${value:.2f}"
    elif value >= 1:
        return f"${value:.4f}"
    elif value >= 0.01:
        return f"${value:.6f}"
    else:
        return f"${value:.10f}"

def get_enhanced_grid_setup(coin, rsi):
    """
    Calculate optimal grid parameters based on:
    - Price volatility
    - Market cap tier
    - Trading volume
    - RSI signals
    - Recent price action
    - Pionex-specific optimizations
    """
    current_price = coin['current_price']
    sparkline = coin['sparkline_in_7d']['price'][-20:]  # Use more data points
    market_cap = coin['market_cap']
    volume = coin['total_volume']
    
    # Calculate volatility
    volatility = calculate_volatility(sparkline)
    market_tier = get_market_cap_tier(market_cap)
    
    # Base parameters by market cap tier (optimized for Pionex)
    tier_params = {
        "mega": {"base_spacing": 0.003, "safety_buffer": 0.08, "max_grids": 200},
        "large": {"base_spacing": 0.004, "safety_buffer": 0.10, "max_grids": 150},
        "mid": {"base_spacing": 0.006, "safety_buffer": 0.12, "max_grids": 100},
        "small": {"base_spacing": 0.008, "safety_buffer": 0.15, "max_grids": 80}
    }
    
    params = tier_params[market_tier]
    
    # Adjust spacing based on volatility
    base_spacing = params["base_spacing"]
    if volatility > 0.20:  # Very high volatility
        spacing_multiplier = 2.0
        grid_mode = "Geometric"
    elif volatility > 0.15:  # High volatility
        spacing_multiplier = 1.6
        grid_mode = "Arithmetic"
    elif volatility > 0.08:  # Medium volatility
        spacing_multiplier = 1.2
        grid_mode = "Arithmetic"
    else:  # Low volatility
        spacing_multiplier = 0.8
        grid_mode = "Arithmetic"
    
    adjusted_spacing = base_spacing * spacing_multiplier
    
    # Calculate price range with enhanced logic
    recent_min = min(sparkline)
    recent_max = max(sparkline)
    recent_range = recent_max - recent_min
    
    # Adjust range based on RSI and volatility
    safety_buffer = params["safety_buffer"]
    if rsi <= 25:  # Extremely oversold
        lower_buffer = safety_buffer * 0.6  # Tighter lower bound
        upper_buffer = safety_buffer * 1.4  # Wider upper bound
    elif rsi <= 35:  # Oversold
        lower_buffer = safety_buffer * 0.8
        upper_buffer = safety_buffer * 1.2
    elif rsi >= 75:  # Extremely overbought
        lower_buffer = safety_buffer * 1.4
        upper_buffer = safety_buffer * 0.6
    elif rsi >= 65:  # Overbought
        lower_buffer = safety_buffer * 1.2
        upper_buffer = safety_buffer * 0.8
    else:  # Neutral
        lower_buffer = upper_buffer = safety_buffer
    
    # Calculate final range
    min_price = recent_min * (1 - lower_buffer)
    max_price = recent_max * (1 + upper_buffer)
    
    # Ensure minimum range for grid spacing
    price_range = max_price - min_price
    min_required_range = current_price * adjusted_spacing * 20  # At least 20 grids
    if price_range < min_required_range:
        center_adjustment = (min_required_range - price_range) / 2
        min_price -= center_adjustment
        max_price += center_adjustment
    
    # Calculate optimal grid count
    grid_spacing = current_price * adjusted_spacing
    theoretical_grids = (max_price - min_price) / grid_spacing
    
    # Apply grid count limits
    max_grids = params["max_grids"]
    if volume > 100_000_000:  # High volume allows more grids
        max_grids = int(max_grids * 1.3)
    elif volume < 20_000_000:  # Low volume needs fewer grids
        max_grids = int(max_grids * 0.7)
    
    optimal_grids = max(15, min(max_grids, int(theoretical_grids)))
    
    # Determine direction based on RSI
    if rsi <= 30:
        direction = "Long"
        direction_confidence = "High"
    elif rsi <= 40:
        direction = "Long"
        direction_confidence = "Medium"
    elif rsi >= 70:
        direction = "Short"
        direction_confidence = "High"
    elif rsi >= 60:
        direction = "Short"
        direction_confidence = "Medium"
    else:
        direction = "Neutral"
        direction_confidence = "High"
    
    # Calculate expected daily cycles based on volatility
    daily_cycles = int(volatility * 100 * 2)  # Rough estimate
    
    # Pionex-specific settings
    trailing_enabled = "Yes" if market_tier in ["mega", "large"] and direction != "Neutral" else "No"
    stop_loss = "5%" if market_tier == "small" and direction != "Neutral" else "Disabled"
    
    return {
        'min_price': min_price,
        'max_price': max_price,
        'grids': optimal_grids,
        'mode': grid_mode,
        'direction': direction,
        'direction_confidence': direction_confidence,
        'spacing': grid_spacing,
        'volatility': volatility,
        'market_tier': market_tier,
        'trailing': trailing_enabled,
        'stop_loss': stop_loss,
        'expected_daily_cycles': daily_cycles
    }

def main():
    try:
        logging.info("Starting Pionex PERP-enhanced grid analysis...")
        market_data = fetch_market_data()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        main_alerts = []
        small_alerts = []

        if not market_data:
            logging.info("No Pionex PERP-compatible market data available, sending empty alert")
            send_telegram(f"🤖 PIONEX FUTURES GRID ALERT — {ts}\n❌ No suitable Pionex PERP-compatible grid trading opportunities this hour.\n💡 Check back later for new opportunities!")
            return

        # Get count of supported tokens for context
        supported_count = len(get_pionex_supported_tokens())

        for coin in market_data:
            id_ = coin['id']
            current_price = coin['current_price']
            symbol = coin['symbol'].upper()
            sparkline = coin['sparkline_in_7d']['price'][-15:]
            rsi = calc_rsi(sparkline)

            if rsi is None:
                continue

            # Get enhanced grid parameters
            grid_params = get_enhanced_grid_setup(coin, rsi)
            
            price_fmt = format_price(current_price)
            low_fmt = format_price(grid_params['min_price'])
            high_fmt = format_price(grid_params['max_price'])
            
            # Create comprehensive alert
            confidence_emoji = "🔥" if grid_params['direction_confidence'] == "High" else "⚡"
            direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[grid_params['direction']]
            
            alert = f"{direction_emoji} {symbol} RSI {rsi:.1f} | {grid_params['market_tier'].upper()}-CAP\n"
            alert += f"📊 PIONEX FUTURES GRID SETUP\n"
            alert += f"• Price Range: {low_fmt} - {high_fmt}\n"
            alert += f"• Grid Count: {grid_params['grids']} grids\n"
            alert += f"• Grid Mode: {grid_params['mode']}\n"
            alert += f"• Direction: {grid_params['direction']} {confidence_emoji}\n"
            alert += f"• Trailing: {grid_params['trailing']}\n"
            alert += f"• Stop Loss: {grid_params['stop_loss']}\n"
            alert += f"• Expected Cycles/Day: ~{grid_params['expected_daily_cycles']}\n"
            alert += f"• Volatility: {grid_params['volatility']:.1%}\n"
            
            # Add reasoning
            if rsi <= 35:
                reason = f"Oversold conditions suggest potential rebound. Recommended for Long bias grid."
            elif rsi >= 65:
                reason = f"Overbought conditions suggest potential decline. Recommended for Short bias grid."
            else:
                reason = f"Neutral RSI perfect for range-bound grid trading. High profit potential from volatility."
            
            alert += f"\n💡 Analysis: {reason}"
            
            if id_ in MAIN_TOKENS:
                main_alerts.append(alert)
            else:
                small_alerts.append(alert)

        # Compose final message
        message = f"🤖 PIONEX FUTURES GRID ALERTS — {ts}\n"
        message += f"📊 Analyzed {supported_count} Pionex PERP-supported tokens\n\n"
        
        if main_alerts:
            message += "🏆 MAIN TOKENS\n" + '\n\n'.join(main_alerts) + '\n\n'
        
        if small_alerts:
            message += "💎 SMALLER OPPORTUNITIES\n" + '\n\n'.join(small_alerts[:2])  # Limit to 2 for message size
        
        if not main_alerts and not small_alerts:
            message += '❌ No suitable grid trading opportunities this hour.\n'
            message += '⏳ Market conditions may be too stable or volatile for optimal grid trading.\n'
            message += f'💡 Monitoring {supported_count} Pionex PERP tokens for next opportunity.'

        logging.info(f"Sending Pionex-enhanced Telegram message: {message[:100]}...")
        send_telegram(message)
        logging.info("Pionex PERP-enhanced grid analysis completed")

    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        send_telegram(f"🚨 Pionex Grid Bot API Error: {e}")
    except Exception as e:
        logging.error(f"Unexpected Error: {e}")
        send_telegram(f"🚨 Pionex Grid Bot Unexpected Error: {e}")

if __name__ == "__main__":
    main()
