import requests
import time
from datetime import datetime, timezone, timedelta
import os
import re
import logging
import math
import statistics

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

# Pionex-specific configuration
PIONEX_CONFIG = {
    'spot_fee': 0.0005,  # 0.05% maker/taker fee
    'futures_fee': 0.0002,  # 0.02% futures fee
    'min_grid_investment': 10,  # $10 minimum
    'max_grids': 150,  # Pionex grid limit
    'min_grids': 7,   # Pionex minimum
    'supported_pairs': [  # Major pairs with good liquidity on Pionex
        'BTC', 'ETH', 'BNB', 'ADA', 'SOL', 'DOT', 'MATIC', 'AVAX', 
        'LINK', 'UNI', 'LTC', 'BCH', 'XRP', 'DOGE', 'SHIB', 'ATOM'
    ]
}

def get_market_session():
    """Determine current market session and activity level"""
    utc_now = datetime.now(timezone.utc)
    hour = utc_now.hour
    
    # Market sessions (UTC)
    sessions = {
        'ASIA': (0, 8),      # 00:00-08:00 UTC (Asia trading hours)
        'EUROPE': (7, 16),   # 07:00-16:00 UTC (Europe overlap)
        'US': (13, 22),      # 13:00-22:00 UTC (US trading hours)
        'QUIET': (22, 24) or (0, 2)  # Low activity periods
    }
    
    active_sessions = []
    for session, (start, end) in sessions.items():
        if session == 'QUIET':
            if hour >= 22 or hour <= 2:
                active_sessions.append(session)
        elif start <= hour < end:
            active_sessions.append(session)
    
    # Determine primary session and activity level
    if 'US' in active_sessions and 'EUROPE' in active_sessions:
        return 'US-EU_OVERLAP', 'HIGH'
    elif 'EUROPE' in active_sessions and 'ASIA' in active_sessions:
        return 'EU-ASIA_OVERLAP', 'HIGH'
    elif 'US' in active_sessions:
        return 'US_SESSION', 'MEDIUM'
    elif 'EUROPE' in active_sessions:
        return 'EU_SESSION', 'MEDIUM'
    elif 'ASIA' in active_sessions:
        return 'ASIA_SESSION', 'MEDIUM'
    else:
        return 'QUIET_HOURS', 'LOW'

def send_telegram(message):
    token_source = "GitHub Secrets" if os.getenv('TELEGRAM_TOKEN') else "fallback"
    chat_id_source = "GitHub Secrets" if os.getenv('TELEGRAM_CHAT_ID') else "fallback"
    logging.info(f"Attempting to send Telegram message using token from {token_source} and chat_id from {chat_id_source}")

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
        error_details = f"Telegram send failed: {e}"
        logging.error(error_details)
        time.sleep(30)
        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            logging.info(f"Telegram retry succeeded: {message[:50]}...")
        except requests.exceptions.RequestException as e2:
            logging.error(f"Telegram retry failed: {e2}")
            return

def fetch_market_data():
    logging.info("Fetching market data from CoinGecko...")
    url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={TOP_COINS_LIMIT}&page=1&sparkline=true"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = [coin for coin in response.json() if coin['total_volume'] > MIN_VOLUME and coin['current_price'] > MIN_PRICE]
    
    # Filter for Pionex supported pairs
    filtered_data = []
    for coin in data:
        symbol = coin['symbol'].upper()
        # Check if it's a supported pair and not a leveraged token
        if (symbol in PIONEX_CONFIG['supported_pairs'] and 
            not re.search(r'(\d+[LS])$', symbol)):
            filtered_data.append(coin)
    
    logging.info(f"Pionex-compatible tokens found: {len(filtered_data)}")
    
    # Apply top coins exclusion but ensure main tokens are included
    smaller_tokens = [coin for coin in filtered_data if filtered_data.index(coin) >= TOP_COINS_TO_EXCLUDE]
    
    # Add main tokens if not already included
    for token_id in MAIN_TOKENS:
        if not any(coin['id'] == token_id for coin in smaller_tokens):
            main_coin = next((coin for coin in data if coin['id'] == token_id), None)
            if main_coin and main_coin['symbol'].upper() in PIONEX_CONFIG['supported_pairs']:
                smaller_tokens.append(main_coin)
    
    return smaller_tokens

def calculate_dynamic_rsi_period(volatility):
    """Calculate optimal RSI period based on volatility"""
    if volatility > 0.25:  # Very high volatility
        return 9   # Shorter period for faster signals
    elif volatility > 0.15:  # High volatility  
        return 11
    elif volatility > 0.08:  # Medium volatility
        return 14  # Standard period
    elif volatility > 0.04:  # Low volatility
        return 18  # Longer period for stability
    else:  # Very low volatility
        return 21

def calc_dynamic_rsi(prices, volatility):
    """Calculate RSI with dynamic period based on volatility"""
    period = calculate_dynamic_rsi_period(volatility)
    
    if len(prices) < period + 1:
        return None
        
    gains = []
    losses = []
    
    for i in range(1, period + 1):
        delta = prices[-i] - prices[-(i + 1)]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD indicator"""
    if len(prices) < slow + signal:
        return None, None, None
    
    # Calculate EMAs
    def ema(data, period):
        multiplier = 2 / (period + 1)
        ema_values = [data[0]]
        for price in data[1:]:
            ema_values.append((price * multiplier) + (ema_values[-1] * (1 - multiplier)))
        return ema_values
    
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    
    # MACD line
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(len(slow_ema))]
    
    # Signal line (EMA of MACD)
    if len(macd_line) >= signal:
        signal_line = ema(macd_line, signal)
        histogram = [macd_line[i] - signal_line[i] for i in range(len(signal_line))]
        return macd_line[-1], signal_line[-1], histogram[-1]
    
    return macd_line[-1], None, None

def calculate_volume_profile_levels(prices, volumes):
    """Calculate key volume profile levels for better range selection"""
    if len(prices) < 20 or len(volumes) < 20:
        return None
    
    # Create price-volume pairs
    pv_pairs = list(zip(prices[-20:], volumes[-20:]))
    
    # Group by price ranges (create bins)
    min_price = min(prices[-20:])
    max_price = max(prices[-20:])
    price_range = max_price - min_price
    
    if price_range == 0:
        return None
    
    num_bins = 10
    bin_size = price_range / num_bins
    volume_bins = {}
    
    for price, volume in pv_pairs:
        bin_index = int((price - min_price) / bin_size)
        bin_index = min(bin_index, num_bins - 1)  # Ensure within bounds
        
        if bin_index not in volume_bins:
            volume_bins[bin_index] = {'volume': 0, 'price_sum': 0, 'count': 0}
        
        volume_bins[bin_index]['volume'] += volume
        volume_bins[bin_index]['price_sum'] += price
        volume_bins[bin_index]['count'] += 1
    
    # Find high volume nodes (support/resistance)
    if not volume_bins:
        return None
    
    # Calculate average price for each bin
    for bin_data in volume_bins.values():
        if bin_data['count'] > 0:
            bin_data['avg_price'] = bin_data['price_sum'] / bin_data['count']
    
    # Sort by volume to find key levels
    sorted_bins = sorted(volume_bins.items(), key=lambda x: x[1]['volume'], reverse=True)
    
    # Extract top 3 volume levels as key support/resistance
    key_levels = []
    for i, (bin_idx, bin_data) in enumerate(sorted_bins[:3]):
        if bin_data['count'] > 0:
            key_levels.append({
                'price': bin_data['avg_price'],
                'volume': bin_data['volume'],
                'strength': 'HIGH' if i == 0 else 'MEDIUM' if i == 1 else 'LOW'
            })
    
    return key_levels

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

def calculate_pionex_fees(investment_amount, grid_count, expected_cycles_per_day):
    """Calculate expected daily fees on Pionex"""
    fee_rate = PIONEX_CONFIG['spot_fee']
    
    # Each grid execution = 2 trades (buy + sell)
    trades_per_cycle = 2
    daily_trades = expected_cycles_per_day * trades_per_cycle
    
    # Average trade size
    avg_trade_size = investment_amount / grid_count
    
    # Daily fee calculation
    daily_fees = daily_trades * avg_trade_size * fee_rate
    daily_fee_percentage = (daily_fees / investment_amount) * 100
    
    return {
        'daily_fees_usd': daily_fees,
        'daily_fee_percentage': daily_fee_percentage,
        'monthly_fees_usd': daily_fees * 30,
        'fee_rate': fee_rate * 100  # Convert to percentage
    }

def format_price(value):
    if value >= 100:
        return f"${value:.2f}"
    elif value >= 1:
        return f"${value:.4f}"
    elif value >= 0.01:
        return f"${value:.6f}"
    else:
        return f"${value:.10f}"

def get_enhanced_pionex_setup(coin, market_session, session_activity):
    """
    Calculate optimal Pionex grid parameters with enhanced analysis
    """
    current_price = coin['current_price']
    sparkline_prices = coin['sparkline_in_7d']['price'][-30:]  # More data for better analysis
    market_cap = coin['market_cap']
    volume = coin['total_volume']
    symbol = coin['symbol'].upper()
    
    # Calculate volatility first (needed for dynamic RSI)
    volatility = calculate_volatility(sparkline_prices)
    
    # Calculate dynamic RSI
    rsi = calc_dynamic_rsi(sparkline_prices, volatility)
    rsi_period = calculate_dynamic_rsi_period(volatility)
    
    if rsi is None:
        return None
    
    # Calculate MACD
    macd_line, signal_line, histogram = calculate_macd(sparkline_prices[-30:])
    
    # Volume profile analysis (simulate volume data if not available)
    simulated_volumes = [volume] * len(sparkline_prices[-20:])  # CoinGecko doesn't provide historical volume in sparkline
    volume_levels = calculate_volume_profile_levels(sparkline_prices[-20:], simulated_volumes)
    
    market_tier = get_market_cap_tier(market_cap)
    
    # Pionex-specific parameters
    pionex_params = {
        "mega": {"base_spacing": 0.004, "safety_buffer": 0.08, "max_grids": 150},
        "large": {"base_spacing": 0.005, "safety_buffer": 0.10, "max_grids": 120},
        "mid": {"base_spacing": 0.007, "safety_buffer": 0.12, "max_grids": 100},
        "small": {"base_spacing": 0.009, "safety_buffer": 0.15, "max_grids": 80}
    }
    
    params = pionex_params[market_tier]
    
    # Adjust for market session
    session_multiplier = {
        'HIGH': 1.2,    # More active during overlaps
        'MEDIUM': 1.0,  # Normal activity
        'LOW': 0.8      # Less active during quiet hours
    }[session_activity]
    
    # Calculate spacing with volatility and session adjustments
    base_spacing = params["base_spacing"] * session_multiplier
    
    if volatility > 0.20:
        spacing_multiplier = 2.2
        grid_mode = "Geometric"
    elif volatility > 0.15:
        spacing_multiplier = 1.7
        grid_mode = "Arithmetic"
    elif volatility > 0.08:
        spacing_multiplier = 1.3
        grid_mode = "Arithmetic"
    else:
        spacing_multiplier = 0.9
        grid_mode = "Arithmetic"
    
    adjusted_spacing = base_spacing * spacing_multiplier
    
    # Enhanced range calculation with volume profile
    recent_min = min(sparkline_prices[-20:])
    recent_max = max(sparkline_prices[-20:])
    
    # Incorporate volume profile levels
    if volume_levels:
        support_levels = [level['price'] for level in volume_levels if level['price'] < current_price]
        resistance_levels = [level['price'] for level in volume_levels if level['price'] > current_price]
        
        if support_levels:
            volume_support = min(support_levels)
            recent_min = min(recent_min, volume_support)
        
        if resistance_levels:
            volume_resistance = max(resistance_levels)
            recent_max = max(recent_max, volume_resistance)
    
    # Dynamic buffer based on multiple factors
    safety_buffer = params["safety_buffer"]
    
    # RSI-based adjustments
    if rsi <= 25:
        lower_buffer = safety_buffer * 0.5
        upper_buffer = safety_buffer * 1.5
    elif rsi <= 35:
        lower_buffer = safety_buffer * 0.7
        upper_buffer = safety_buffer * 1.3
    elif rsi >= 75:
        lower_buffer = safety_buffer * 1.5
        upper_buffer = safety_buffer * 0.5
    elif rsi >= 65:
        lower_buffer = safety_buffer * 1.3
        upper_buffer = safety_buffer * 0.7
    else:
        lower_buffer = upper_buffer = safety_buffer
    
    # MACD adjustments
    macd_signal = "NEUTRAL"
    if macd_line is not None and signal_line is not None:
        if macd_line > signal_line and histogram > 0:
            macd_signal = "BULLISH"
            lower_buffer *= 0.8  # Tighter lower bound
            upper_buffer *= 1.2  # Wider upper bound
        elif macd_line < signal_line and histogram < 0:
            macd_signal = "BEARISH"
            lower_buffer *= 1.2
            upper_buffer *= 0.8
    
    # Calculate final range
    min_price = recent_min * (1 - lower_buffer)
    max_price = recent_max * (1 + upper_buffer)
    
    # Ensure Pionex-compatible grid count
    grid_spacing = current_price * adjusted_spacing
    theoretical_grids = (max_price - min_price) / grid_spacing
    
    # Apply Pionex limits
    max_grids = min(params["max_grids"], PIONEX_CONFIG['max_grids'])
    optimal_grids = max(PIONEX_CONFIG['min_grids'], min(max_grids, int(theoretical_grids)))
    
    # Direction logic with multiple confirmations
    direction_score = 0
    
    # RSI contribution
    if rsi <= 30:
        direction_score += 2
    elif rsi <= 40:
        direction_score += 1
    elif rsi >= 70:
        direction_score -= 2
    elif rsi >= 60:
        direction_score -= 1
    
    # MACD contribution
    if macd_signal == "BULLISH":
        direction_score += 1
    elif macd_signal == "BEARISH":
        direction_score -= 1
    
    # Session activity contribution
    if session_activity == "HIGH":
        direction_confidence_boost = True
    else:
        direction_confidence_boost = False
    
    # Final direction determination
    if direction_score >= 2:
        direction = "Long"
        direction_confidence = "High" if direction_confidence_boost else "Medium"
    elif direction_score >= 1:
        direction = "Long"
        direction_confidence = "Medium"
    elif direction_score <= -2:
        direction = "Short"
        direction_confidence = "High" if direction_confidence_boost else "Medium"
    elif direction_score <= -1:
        direction = "Short"
        direction_confidence = "Medium"
    else:
        direction = "Neutral"
        direction_confidence = "High"
    
    # Calculate expected performance
    base_cycles = volatility * 100 * 2
    session_cycles_multiplier = {'HIGH': 1.3, 'MEDIUM': 1.0, 'LOW': 0.7}[session_activity]
    expected_daily_cycles = int(base_cycles * session_cycles_multiplier)
    
    # Investment recommendations
    min_investment = max(50, optimal_grids * 2)  # $2 per grid minimum
    recommended_investment = min_investment * 2  # Conservative recommendation
    
    # Fee calculations
    fee_analysis = calculate_pionex_fees(recommended_investment, optimal_grids, expected_daily_cycles)
    
    return {
        'min_price': min_price,
        'max_price': max_price,
        'grids': optimal_grids,
        'mode': grid_mode,
        'direction': direction,
        'direction_confidence': direction_confidence,
        'rsi': rsi,
        'rsi_period': rsi_period,
        'macd_signal': macd_signal,
        'spacing': grid_spacing,
        'volatility': volatility,
        'market_tier': market_tier,
        'volume_levels': volume_levels,
        'expected_daily_cycles': expected_daily_cycles,
        'min_investment': min_investment,
        'recommended_investment': recommended_investment,
        'fee_analysis': fee_analysis,
        'market_session': market_session,
        'session_activity': session_activity,
        'pionex_compatible': symbol in PIONEX_CONFIG['supported_pairs']
    }

def main():
    try:
        logging.info("Starting enhanced Pionex grid analysis...")
        market_data = fetch_market_data()
        market_session, session_activity = get_market_session()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        main_alerts = []
        small_alerts = []

        if not market_data:
            logging.info("No market data available")
            send_telegram(f"*📊 PIONEX GRID ALERTS — {ts}*\n🔍 No suitable opportunities found for current market conditions.")
            return

        for coin in market_data:
            symbol = coin['symbol'].upper()
            
            # Skip if not supported on Pionex
            if symbol not in PIONEX_CONFIG['supported_pairs']:
                continue
            
            grid_params = get_enhanced_pionex_setup(coin, market_session, session_activity)
            
            if not grid_params:
                continue
            
            current_price = coin['current_price']
            price_fmt = format_price(current_price)
            low_fmt = format_price(grid_params['min_price'])
            high_fmt = format_price(grid_params['max_price'])
            
            # Create comprehensive Pionex-specific alert
            confidence_emoji = "🔥" if grid_params['direction_confidence'] == "High" else "⚡"
            direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[grid_params['direction']]
            session_emoji = {"HIGH": "🔥", "MEDIUM": "⚡", "LOW": "💤"}[grid_params['session_activity']]
            
            alert = f"{direction_emoji} *{symbol}* | {grid_params['market_tier'].upper()}-CAP\n"
            alert += f"📊 *PIONEX GRID SETUP*\n"
            alert += f"• Range: `{low_fmt} - {high_fmt}`\n"
            alert += f"• Grids: `{grid_params['grids']}` ({grid_params['mode']})\n"
            alert += f"• Direction: `{grid_params['direction']}` {confidence_emoji}\n"
            alert += f"• Investment: `${grid_params['min_investment']}-${grid_params['recommended_investment']}`\n"
            
            alert += f"\n🎯 *SIGNALS*\n"
            alert += f"• RSI({grid_params['rsi_period']}): `{grid_params['rsi']:.1f}`\n"
            alert += f"• MACD: `{grid_params['macd_signal']}`\n"
            alert += f"• Volatility: `{grid_params['volatility']:.1%}`\n"
            alert += f"• Session: `{market_session}` {session_emoji}\n"
            
            alert += f"\n💰 *PERFORMANCE*\n"
            alert += f"• Expected Cycles/Day: `{grid_params['expected_daily_cycles']}`\n"
            alert += f"• Daily Fees: `${grid_params['fee_analysis']['daily_fees_usd']:.2f}` ({grid_params['fee_analysis']['daily_fee_percentage']:.2f}%)\n"
            alert += f"• Pionex Fee Rate: `{grid_params['fee_analysis']['fee_rate']:.2f}%`\n"
            
            # Add volume profile info if available
            if grid_params['volume_levels']:
                strong_levels = [level for level in grid_params['volume_levels'] if level['strength'] == 'HIGH']
                if strong_levels:
                    alert += f"• Key Level: `{format_price(strong_levels[0]['price'])}` (High Volume)\n"
            
            # Reasoning
            if grid_params['rsi'] <= 35 and grid_params['macd_signal'] == "BULLISH":
                reason = "Strong oversold + MACD bullish divergence. Excellent Long setup."
            elif grid_params['rsi'] >= 65 and grid_params['macd_signal'] == "BEARISH":
                reason = "Overbought + MACD bearish. Good Short opportunity."
            elif 40 <= grid_params['rsi'] <= 60:
                reason = f"Perfect range conditions. High profit potential from {grid_params['volatility']:.1%} volatility."
            else:
                reason = f"RSI {grid_params['rsi']:.0f} suggests {grid_params['direction'].lower()} bias with {grid_params['direction_confidence'].lower()} confidence."
            
            alert += f"\n💡 *Analysis*: {reason}"
            
            if coin['id'] in MAIN_TOKENS:
                main_alerts.append(alert)
            else:
                small_alerts.append(alert)

        # Compose final message
        message = f"*🤖 PIONEX GRID TRADING — {ts}*\n"
        message += f"📍 Session: *{market_session}* {session_emoji}\n\n"
        
        if main_alerts:
            message += "*🏆 MAIN TOKENS*\n" + '\n\n'.join(main_alerts) + '\n\n'
        
        if small_alerts:
            message += "*💎 ALTCOIN OPPORTUNITIES*\n" + '\n\n'.join(small_alerts[:2])
        
        if not main_alerts and not small_alerts:
            message += '❌ No Pionex-compatible opportunities found.\n'
            message += f'⏳ Market session: {market_session} - Activity: {session_activity}'

        # Pionex-specific footer
        message += f"\n\n*📱 PIONEX SETUP GUIDE:*\n"
        message += f"1. Open Pionex → Grid Trading → Spot Grid\n"
        message += f"2. Select token pair (USDT)\n"
        message += f"3. Set AI Strategy → Manual\n"
        message += f"4. Input exact price range shown\n"
        message += f"5. Set grid quantity as recommended\n"
        message += f"6. Choose Arithmetic/Geometric mode\n"
        message += f"7. Set investment amount (min shown)\n"
        message += f"\n⚠️ *Pionex Fee: 0.05% per trade | Always DYOR!*"

        logging.info(f"Sending Pionex-optimized message...")
        send_telegram(message)
        logging.info("Enhanced Pionex analysis completed")

    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        send_telegram(f"⚠️ API Error: {e}")
    except Exception as e:
        logging.error(f"Unexpected Error: {e}")
        send_telegram(f"⚠️ System Error: {e}")

if __name__ == "__main__":
    main()
