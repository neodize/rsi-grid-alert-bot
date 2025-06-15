import requests
import time
from datetime import datetime, timezone
import os
import re
import logging
import math
from functools import wraps

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')
COINGECKO_API = 'https://api.coingecko.com/api/v3'
PIONEX_API = 'https://api.pionex.com/api/v1'
TOP_COINS_LIMIT = 100
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
TOP_COINS_TO_EXCLUDE = 20
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']

# Rate limiting configuration
PIONEX_RATE_LIMIT = 10  # Max requests per minute
COINGECKO_RATE_LIMIT = 30  # Max requests per minute
REQUEST_DELAYS = {
    'pionex': 60 / PIONEX_RATE_LIMIT,  # 6 seconds between requests
    'coingecko': 60 / COINGECKO_RATE_LIMIT  # 2 seconds between requests
}

# Global rate limiting trackers
last_request_times = {
    'pionex': 0,
    'coingecko': 0
}
pionex_supported_tokens = None
pionex_last_fetch = 0
PIONEX_CACHE_DURATION = 3600  # Cache for 1 hour

# Comprehensive exclusion lists
WRAPPED_TOKENS = {
    'WBTC', 'WETH', 'WBNB', 'WMATIC', 'WAVAX', 'WFTM', 'WONE', 'WROSE',
    'CBBTC', 'CBETH', 'RETH', 'STETH', 'WSTETH', 'FRXETH', 'SFRXETH',
    'WSOL', 'MSOL', 'STSOL', 'JSOL', 'BSOL', 'BONK', 'WIF'
}

STABLECOINS = {
    'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'USDD', 'FRAX', 'LUSD',
    'GUSD', 'USDC.E', 'USDT.E', 'FDUSD', 'PYUSD', 'USDB', 'USDE', 'CRVUSD',
    'SUSD', 'DUSD', 'OUSD', 'USTC', 'USDK', 'USDN', 'USDS', 'USDY'
}

EXCLUDED_TOKENS = {
    'ETHUP', 'ETHDOWN', 'BTCUP', 'BTCDOWN', 'ADAUP', 'ADADOWN',
    'SYNTH', 'PERP', 
    'SHIB', 'DOGE', 'PEPE', 'FLOKI', 'BABYDOGE',
    'LUNA', 'LUNC', 'USTC'
}

def rate_limit(api_name):
    """Decorator to enforce rate limiting"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            global last_request_times
            
            current_time = time.time()
            time_since_last = current_time - last_request_times[api_name]
            required_delay = REQUEST_DELAYS[api_name]
            
            if time_since_last < required_delay:
                sleep_time = required_delay - time_since_last
                logging.info(f"Rate limiting {api_name}: sleeping for {sleep_time:.2f} seconds")
                time.sleep(sleep_time)
            
            last_request_times[api_name] = time.time()
            return func(*args, **kwargs)
        return wrapper
    return decorator

@rate_limit('pionex')
def fetch_pionex_supported_tokens():
    """Fetch supported tokens from Pionex API with caching and rate limiting"""
    global pionex_supported_tokens, pionex_last_fetch
    
    # Check cache first
    current_time = time.time()
    if (pionex_supported_tokens is not None and 
        current_time - pionex_last_fetch < PIONEX_CACHE_DURATION):
        logging.info("Using cached Pionex supported tokens")
        return pionex_supported_tokens
    
    logging.info("Fetching supported tokens from Pionex API...")
    
    try:
        response = requests.get(f"{PIONEX_API}/common/symbols", timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data.get('result') == 'true' and 'data' in data:
            symbols_data = data['data']
            
            # Extract PERP and SPOT pairs
            perp_tokens = set()
            spot_tokens = set()
            
            for symbol_info in symbols_data:
                symbol = symbol_info.get('symbol', '')
                if symbol.endswith('_USDT_PERP'):
                    token = symbol.replace('_USDT_PERP', '')
                    perp_tokens.add(token)
                elif symbol.endswith('_USDT'):
                    token = symbol.replace('_USDT', '')
                    spot_tokens.add(token)
            
            pionex_supported_tokens = {
                'perp': perp_tokens,
                'spot': spot_tokens,
                'perp_count': len(perp_tokens),
                'spot_count': len(spot_tokens)
            }
            pionex_last_fetch = current_time
            
            logging.info(f"Pionex supports {len(perp_tokens)} PERP tokens and {len(spot_tokens)} SPOT tokens")
            logging.info(f"Sample PERP tokens: {list(perp_tokens)[:10]}")
            
            return pionex_supported_tokens
            
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch Pionex supported tokens: {e}")
        # Return empty set to allow all tokens if API fails
        if pionex_supported_tokens is None:
            pionex_supported_tokens = {
                'perp': set(),
                'spot': set(),
                'perp_count': 0,
                'spot_count': 0
            }
            logging.warning("No Pionex supported tokens available, allowing all tokens")
        
        return pionex_supported_tokens

def is_excluded_token(symbol, name):
    """Check if a token should be excluded based on symbol and name"""
    symbol_upper = symbol.upper()
    name_upper = name.upper() if name else ""
    
    if symbol_upper in WRAPPED_TOKENS:
        return True, "wrapped"
    
    if symbol_upper in STABLECOINS:
        return True, "stablecoin"
    
    if symbol_upper in EXCLUDED_TOKENS:
        return True, "excluded"
    
    if re.search(r'(\d+[LS])$', symbol_upper):
        return True, "leveraged"
    
    if (symbol_upper.startswith('W') and len(symbol_upper) > 1 and 
        symbol_upper[1:] in ['BTC', 'ETH', 'SOL', 'BNB', 'MATIC', 'AVAX']):
        return True, "wrapped_pattern"
    
    if any(pattern in name_upper for pattern in ['USD COIN', 'TETHER', 'BINANCE USD', 'DAI STABLECOIN']):
        return True, "stablecoin_name"
    
    if any(pattern in name_upper for pattern in ['WRAPPED', 'WORMHOLE', 'BRIDGE']):
        return True, "wrapped_name"
    
    return False, None

def send_telegram(message):
    """Send message to Telegram with retry logic"""
    if not TELEGRAM_TOKEN.strip() or not TELEGRAM_CHAT_ID.strip():
        logging.warning("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is empty, skipping message")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            logging.info(f"Telegram message sent successfully: {message[:50]}...")
            return
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait_time = 30 * (attempt + 1)
                logging.warning(f"Telegram send failed (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logging.error(f"Failed to send Telegram message after {max_retries + 1} attempts: {e}")

@rate_limit('coingecko')
def fetch_market_data():
    """Fetch market data from CoinGecko with rate limiting"""
    logging.info("Fetching market data from CoinGecko...")
    
    # Get Pionex supported tokens first
    pionex_tokens = fetch_pionex_supported_tokens()
    
    url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={TOP_COINS_LIMIT}&page=1&sparkline=true"
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        # Initial filtering by volume and price
        data = [coin for coin in response.json() 
                if coin['total_volume'] > MIN_VOLUME and coin['current_price'] > MIN_PRICE]
        logging.info(f"After volume/price filter: {len(data)} coins")
        
        # Filter out unwanted tokens
        filtered_data = []
        excluded_count = {
            "wrapped": 0, "stablecoin": 0, "leveraged": 0, "excluded": 0, 
            "wrapped_pattern": 0, "stablecoin_name": 0, "wrapped_name": 0
        }
        
        for coin in data:
            is_excluded, reason = is_excluded_token(coin['symbol'], coin['name'])
            if is_excluded:
                excluded_count[reason] += 1
            else:
                # Check if token is supported by Pionex (optional filter)
                symbol_upper = coin['symbol'].upper()
                if (pionex_tokens['perp_count'] > 0 and 
                    symbol_upper not in pionex_tokens['perp'] and 
                    symbol_upper not in pionex_tokens['spot'] and
                    coin['id'] not in MAIN_TOKENS):
                    logging.debug(f"Token {symbol_upper} not supported by Pionex, skipping")
                    continue
                
                filtered_data.append(coin)
        
        logging.info(f"Exclusion summary: {dict(excluded_count)}")
        logging.info(f"After filtering: {len(filtered_data)} coins")
        
        # Separate main tokens from smaller tokens
        main_tokens_found = [coin for coin in filtered_data if coin['id'] in MAIN_TOKENS]
        smaller_tokens = [coin for i, coin in enumerate(filtered_data) 
                         if i >= TOP_COINS_TO_EXCLUDE and coin['id'] not in MAIN_TOKENS]
        
        # Try to fetch missing main tokens
        missing_main_tokens = [token_id for token_id in MAIN_TOKENS 
                              if not any(coin['id'] == token_id for coin in main_tokens_found)]
        
        for token_id in missing_main_tokens:
            if fetch_missing_main_token(token_id, main_tokens_found):
                time.sleep(REQUEST_DELAYS['coingecko'])  # Rate limit between requests
        
        final_tokens = main_tokens_found + smaller_tokens
        logging.info(f"Final: {len(main_tokens_found)} main + {len(smaller_tokens)} smaller = {len(final_tokens)} total")
        
        return final_tokens
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch market data: {e}")
        return []

@rate_limit('coingecko')
def fetch_missing_main_token(token_id, main_tokens_found):
    """Fetch a missing main token with rate limiting"""
    logging.info(f"Fetching missing main token: {token_id}")
    
    variants_to_try = HYPE_VARIANTS if token_id == 'hyperliquid' else [token_id]
    
    for variant in variants_to_try:
        try:
            direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={variant}&sparkline=true"
            direct_response = requests.get(direct_url, timeout=10)
            direct_response.raise_for_status()
            direct_data = direct_response.json()
            
            if (direct_data and len(direct_data) > 0 and 
                direct_data[0]['total_volume'] > MIN_VOLUME and 
                direct_data[0]['current_price'] > MIN_PRICE):
                
                is_excluded, reason = is_excluded_token(direct_data[0]['symbol'], direct_data[0]['name'])
                if not is_excluded:
                    logging.info(f"Successfully fetched {variant}: {direct_data[0]['symbol']}")
                    main_tokens_found.append(direct_data[0])
                    return True
                    
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch {variant}: {e}")
            
        # Rate limit between variant attempts
        if len(variants_to_try) > 1:
            time.sleep(REQUEST_DELAYS['coingecko'])
    
    return False

def calc_rsi(prices):
    """Calculate RSI from price array"""
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
        return 0.05
    
    returns = []
    for i in range(1, len(prices)):
        if prices[i-1] > 0:
            returns.append(abs(prices[i] / prices[i-1] - 1))
    
    return sum(returns) / len(returns) if returns else 0.05

def get_market_cap_tier(market_cap):
    """Classify market cap into tiers"""
    if market_cap >= 50_000_000_000:
        return "mega"
    elif market_cap >= 10_000_000_000:
        return "large"
    elif market_cap >= 1_000_000_000:
        return "mid"
    else:
        return "small"

def format_price(value):
    """Format price with appropriate decimal places"""
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
    """Calculate optimal grid parameters"""
    current_price = coin['current_price']
    sparkline = coin['sparkline_in_7d']['price'][-20:]
    market_cap = coin['market_cap']
    volume = coin['total_volume']
    
    volatility = calculate_volatility(sparkline)
    market_tier = get_market_cap_tier(market_cap)
    
    tier_params = {
        "mega": {"base_spacing": 0.003, "safety_buffer": 0.08, "max_grids": 150},
        "large": {"base_spacing": 0.004, "safety_buffer": 0.10, "max_grids": 120},
        "mid": {"base_spacing": 0.006, "safety_buffer": 0.12, "max_grids": 100},
        "small": {"base_spacing": 0.008, "safety_buffer": 0.15, "max_grids": 80}
    }
    
    params = tier_params[market_tier]
    
    # Adjust spacing based on volatility
    base_spacing = params["base_spacing"]
    if volatility > 0.20:
        spacing_multiplier = 2.0
        grid_mode = "Geometric"
    elif volatility > 0.15:
        spacing_multiplier = 1.6
        grid_mode = "Arithmetic"
    elif volatility > 0.08:
        spacing_multiplier = 1.2
        grid_mode = "Arithmetic"
    else:
        spacing_multiplier = 0.8
        grid_mode = "Arithmetic"
    
    adjusted_spacing = base_spacing * spacing_multiplier
    
    # Calculate price range
    recent_min = min(sparkline)
    recent_max = max(sparkline)
    
    safety_buffer = params["safety_buffer"]
    if rsi <= 25:
        lower_buffer = safety_buffer * 0.6
        upper_buffer = safety_buffer * 1.4
    elif rsi <= 35:
        lower_buffer = safety_buffer * 0.8
        upper_buffer = safety_buffer * 1.2
    elif rsi >= 75:
        lower_buffer = safety_buffer * 1.4
        upper_buffer = safety_buffer * 0.6
    elif rsi >= 65:
        lower_buffer = safety_buffer * 1.2
        upper_buffer = safety_buffer * 0.8
    else:
        lower_buffer = upper_buffer = safety_buffer
    
    min_price = recent_min * (1 - lower_buffer)
    max_price = recent_max * (1 + upper_buffer)
    
    # Ensure minimum range
    price_range = max_price - min_price
    min_required_range = current_price * adjusted_spacing * 20
    if price_range < min_required_range:
        center_adjustment = (min_required_range - price_range) / 2
        min_price -= center_adjustment
        max_price += center_adjustment
    
    # Calculate optimal grid count
    grid_spacing = current_price * adjusted_spacing
    theoretical_grids = (max_price - min_price) / grid_spacing
    
    max_grids = params["max_grids"]
    if volume > 100_000_000:
        max_grids = int(max_grids * 1.3)
    elif volume < 20_000_000:
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
    
    daily_cycles = int(volatility * 100 * 2)
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
    """Main execution function"""
    try:
        logging.info("Starting enhanced grid analysis with rate limiting...")
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
            symbol = coin['symbol'].upper()
            sparkline = coin['sparkline_in_7d']['price'][-15:]
            rsi = calc_rsi(sparkline)

            if rsi is None:
                continue

            grid_params = get_enhanced_grid_setup(coin, rsi)
            
            price_fmt = format_price(coin['current_price'])
            low_fmt = format_price(grid_params['min_price'])
            high_fmt = format_price(grid_params['max_price'])
            
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

        # Compose final message
        message = f"*🤖 ENHANCED GRID TRADING ALERTS — {escape_markdown(ts)}*\n\n"
        
        if main_alerts:
            message += "*🏆 MAIN TOKENS*\n" + '\n\n'.join(main_alerts) + '\n\n'
        
        if small_alerts:
            message += "*💎 SMALLER OPPORTUNITIES*\n" + '\n\n'.join(small_alerts[:2])
        
        if not main_alerts and not small_alerts:
            message += '❌ No suitable grid trading opportunities this hour\\.\n'
            message += '⏳ Market conditions may be too stable or volatile for optimal grid trading\\.'

        logging.info("Sending enhanced Telegram message...")
        send_telegram(message)
        logging.info("Enhanced grid analysis completed successfully")

    except Exception as e:
        logging.error(f"Unexpected Error in main(): {e}")
        send_telegram(f"Unexpected Error: {str(e)[:100]}")

if __name__ == "__main__":
    main()
