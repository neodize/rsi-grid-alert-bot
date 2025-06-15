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
TOP_COINS_LIMIT = 100  # Increased to ensure we get main tokens
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
TOP_COINS_TO_EXCLUDE = 20
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']

# Comprehensive exclusion lists
WRAPPED_TOKENS = {
    'WBTC', 'WETH', 'WBNB', 'WMATIC', 'WAVAX', 'WFTM', 'WONE', 'WROSE',
    'CBBTC', 'CBETH', 'RETH', 'STETH', 'WSTETH', 'FRXETH', 'SFRXETH',
    'WSOL', 'MSOL', 'STSOL', 'JSOL', 'BSOL', 'BONK', 'WIF'  # Some wrapped SOL variants
}

STABLECOINS = {
    'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'USDD', 'FRAX', 'LUSD',
    'GUSD', 'USDC.E', 'USDT.E', 'FDUSD', 'PYUSD', 'USDB', 'USDE', 'CRVUSD',
    'SUSD', 'DUSD', 'OUSD', 'USTC', 'USDK', 'USDN', 'USDS', 'USDY'
}

EXCLUDED_TOKENS = {
    # Leveraged tokens
    'ETHUP', 'ETHDOWN', 'BTCUP', 'BTCDOWN', 'ADAUP', 'ADADOWN',
    # Synthetic/derivative tokens
    'SYNTH', 'PERP', 
    # Meme tokens with questionable utility (optional - remove if you want these)
    'SHIB', 'DOGE', 'PEPE', 'FLOKI', 'BABYDOGE',
    # Other problematic tokens
    'LUNA', 'LUNC', 'USTC'  # Terra ecosystem tokens
}

def is_excluded_token(symbol, name):
    """
    Check if a token should be excluded based on symbol and name
    """
    symbol_upper = symbol.upper()
    name_upper = name.upper() if name else ""
    
    # Check wrapped tokens
    if symbol_upper in WRAPPED_TOKENS:
        return True, "wrapped"
    
    # Check stablecoins
    if symbol_upper in STABLECOINS:
        return True, "stablecoin"
    
    # Check explicitly excluded tokens
    if symbol_upper in EXCLUDED_TOKENS:
        return True, "excluded"
    
    # Check leveraged tokens (numbers + L/S pattern)
    if re.search(r'(\d+[LS])$', symbol_upper):
        return True, "leveraged"
    
    # Check for common wrapped patterns
    if (symbol_upper.startswith('W') and len(symbol_upper) > 1 and 
        symbol_upper[1:] in ['BTC', 'ETH', 'SOL', 'BNB', 'MATIC', 'AVAX']):
        return True, "wrapped_pattern"
    
    # Check for USD/stable patterns in name
    if any(pattern in name_upper for pattern in ['USD COIN', 'TETHER', 'BINANCE USD', 'DAI STABLECOIN']):
        return True, "stablecoin_name"
    
    # Check for wrapped patterns in name
    if any(pattern in name_upper for pattern in ['WRAPPED', 'WORMHOLE', 'BRIDGE']):
        return True, "wrapped_name"
    
    return False, None

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
    
    # Initial filtering by volume and price
    data = [coin for coin in response.json() if coin['total_volume'] > MIN_VOLUME and coin['current_price'] > MIN_PRICE]
    logging.info(f"After volume/price filter: {len(data)} coins")
    
    # Filter out unwanted tokens - Fixed: Initialize with all possible keys
    filtered_data = []
    excluded_count = {
        "wrapped": 0, 
        "stablecoin": 0, 
        "leveraged": 0, 
        "excluded": 0, 
        "wrapped_pattern": 0,
        "stablecoin_name": 0,
        "wrapped_name": 0
    }
    
    for coin in data:
        is_excluded, reason = is_excluded_token(coin['symbol'], coin['name'])
        if is_excluded:
            excluded_count[reason] += 1
            logging.debug(f"Excluded {coin['symbol']} ({coin['name']}) - Reason: {reason}")
        else:
            filtered_data.append(coin)
    
    logging.info(f"Exclusion summary: {dict(excluded_count)}")
    logging.info(f"After token filtering: {len(filtered_data)} coins")
    
    # Separate main tokens from smaller tokens
    main_tokens_found = []
    smaller_tokens = []
    
    # First, find main tokens in the filtered data
    for coin in filtered_data:
        if coin['id'] in MAIN_TOKENS:
            main_tokens_found.append(coin)
            logging.info(f"Found main token in filtered data: {coin['symbol']} ({coin['id']})")
    
    # Get smaller tokens (excluding top 20 from filtered data)
    for i, coin in enumerate(filtered_data):
        if i >= TOP_COINS_TO_EXCLUDE and coin['id'] not in MAIN_TOKENS:
            smaller_tokens.append(coin)
    
    logging.info(f"Main tokens found: {len(main_tokens_found)}")
    logging.info(f"Smaller tokens after top {TOP_COINS_TO_EXCLUDE} exclusion: {len(smaller_tokens)}")
    
    # Try to fetch missing main tokens directly
    missing_main_tokens = [token_id for token_id in MAIN_TOKENS 
                          if not any(coin['id'] == token_id for coin in main_tokens_found)]
    
    for token_id in missing_main_tokens:
        logging.info(f"Attempting to fetch missing main token: {token_id}")
        
        if token_id == 'hyperliquid':
            # Special handling for hyperliquid variants
            for variant in HYPE_VARIANTS:
                for attempt in range(3):
                    try:
                        direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={variant}&sparkline=true"
                        direct_response = requests.get(direct_url, timeout=10)
                        direct_response.raise_for_status()
                        direct_data = direct_response.json()
                        
                        if (direct_data and len(direct_data) > 0 and 
                            direct_data[0]['total_volume'] > MIN_VOLUME and 
                            direct_data[0]['current_price'] > MIN_PRICE):
                            
                            # Check if this variant should be excluded
                            is_excluded, reason = is_excluded_token(direct_data[0]['symbol'], direct_data[0]['name'])
                            if not is_excluded:
                                logging.info(f"Successfully fetched {variant}: {direct_data[0]['symbol']}")
                                main_tokens_found.append(direct_data[0])
                                break
                            else:
                                logging.warning(f"Fetched {variant} but it was excluded: {reason}")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"Fetch attempt {attempt + 1} for {variant} failed: {e}")
                        if attempt < 2:
                            time.sleep(2 ** attempt)
                        else:
                            logging.error(f"Failed to fetch {variant} after 3 attempts")
                
                # If successful, break out of variant loop
                if any(coin['id'] == variant for coin in main_tokens_found):
                    break
        else:
            # Direct fetch for other main tokens
            for attempt in range(3):
                try:
                    direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={token_id}&sparkline=true"
                    direct_response = requests.get(direct_url, timeout=10)
                    direct_response.raise_for_status()
                    direct_data = direct_response.json()
                    
                    if (direct_data and len(direct_data) > 0 and 
                        direct_data[0]['total_volume'] > MIN_VOLUME and 
                        direct_data[0]['current_price'] > MIN_PRICE):
                        
                        # Check if this token should be excluded
                        is_excluded, reason = is_excluded_token(direct_data[0]['symbol'], direct_data[0]['name'])
                        if not is_excluded:
                            logging.info(f"Successfully fetched {token_id}: {direct_data[0]['symbol']}")
                            main_tokens_found.append(direct_data[0])
                            break
                        else:
                            logging.warning(f"Fetched {token_id} but it was excluded: {reason}")
                except requests.exceptions.RequestException as e:
                    logging.error(f"Direct fetch attempt {attempt + 1} for {token_id} failed: {e}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                    else:
                        logging.error(f"Failed to fetch {token_id} after 3 attempts")
    
    # Combine main tokens and smaller tokens
    final_tokens = main_tokens_found + smaller_tokens
    
    logging.info(f"Final token breakdown:")
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

def escape_markdown(text):
    """Escape special characters for Telegram markdown"""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def get_enhanced_grid_setup(coin, rsi):
    """
    Calculate optimal grid parameters based on:
    - Price volatility
    - Market cap tier
    - Trading volume
    - RSI signals
    - Recent price action
    """
    current_price = coin['current_price']
    sparkline = coin['sparkline_in_7d']['price'][-20:]  # Use more data points
    market_cap = coin['market_cap']
    volume = coin['total_volume']
    
    # Calculate volatility
    volatility = calculate_volatility(sparkline)
    market_tier = get_market_cap_tier(market_cap)
    
    # Base parameters by market cap tier
    tier_params = {
        "mega": {"base_spacing": 0.003, "safety_buffer": 0.08, "max_grids": 150},
        "large": {"base_spacing": 0.004, "safety_buffer": 0.10, "max_grids": 120},
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
    
    # Advanced settings
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
        logging.info("Starting enhanced grid analysis...")
        market_data = fetch_market_data()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        main_alerts = []
        small_alerts = []

        if not market_data:
            logging.info("No market data available, sending empty alert")
            send_telegram(f"*ENHANCED GRID TRADING ALERT — {ts}*\nNo suitable grid trading opportunities this hour.")
            return

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
            
            # Create comprehensive alert with proper markdown escaping
            confidence_emoji = "🔥" if grid_params['direction_confidence'] == "High" else "⚡"
            direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[grid_params['direction']]
            
            alert = f"{direction_emoji} *{symbol}* RSI {rsi:.1f} \\| {grid_params['market_tier'].upper()}\\-CAP\n"
            alert += f"📊 *COMPLETE GRID SETUP*\n"
            alert += f"• Price Range: `{escape_markdown(low_fmt)} - {escape_markdown(high_fmt)}`\n"
            alert += f"• Grid Count: `{grid_params['grids']} grids`\n"
            alert += f"• Grid Mode: `{grid_params['mode']}`\n"
            alert += f"• Direction: `{grid_params['direction']}` {confidence_emoji}\n"
            alert += f"• Trailing: `{grid_params['trailing']}`\n"
            alert += f"• Stop Loss: `{grid_params['stop_loss']}`\n"
            alert += f"• Expected Cycles/Day: `~{grid_params['expected_daily_cycles']}`\n"
            alert += f"• Volatility: `{grid_params['volatility']:.1%}` \\({grid_params['mode']} recommended\\)\n"
            
            # Add reasoning
            if rsi <= 35:
                reason = f"Oversold conditions suggest potential rebound\\. Recommended for Long bias grid\\."
            elif rsi >= 65:
                reason = f"Overbought conditions suggest potential decline\\. Recommended for Short bias grid\\."
            else:
                reason = f"Neutral RSI perfect for range\\-bound grid trading\\. High profit potential from volatility\\."
            
            alert += f"\n💡 *Analysis*: {reason}"
            
            if id_ in MAIN_TOKENS:
                main_alerts.append(alert)
            else:
                small_alerts.append(alert)

        # Compose final message with proper escaping
        message = f"*🤖 ENHANCED GRID TRADING ALERTS — {escape_markdown(ts)}*\n\n"
        
        if main_alerts:
            message += "*🏆 MAIN TOKENS*\n" + '\n\n'.join(main_alerts) + '\n\n'
        
        if small_alerts:
            message += "*💎 SMALLER OPPORTUNITIES*\n" + '\n\n'.join(small_alerts[:2])  # Limit to 2 for message size
        
        if not main_alerts and not small_alerts:
            message += '❌ No suitable grid trading opportunities this hour\\.\n'
            message += '⏳ Market conditions may be too stable or volatile for optimal grid trading\\.'

        logging.info(f"Sending enhanced Telegram message: {message[:100]}...")
        send_telegram(message)
        logging.info("Enhanced grid analysis completed")

    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        send_telegram(f"API Error: {e}")
    except Exception as e:
        logging.error(f"Unexpected Error: {e}")
        send_telegram(f"Unexpected Error: {e}")

if __name__ == "__main__":
    main()
