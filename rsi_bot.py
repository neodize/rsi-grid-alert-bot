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
TOP_COINS_LIMIT = 50
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
TOP_COINS_TO_EXCLUDE = 20
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']

# Opportunity detection thresholds
RSI_OVERSOLD_THRESHOLD = 30
RSI_OVERBOUGHT_THRESHOLD = 70
HIGH_VOLATILITY_THRESHOLD = 0.15  # 15%
FORCE_DAILY_SUMMARY = os.getenv('FORCE_DAILY_SUMMARY', 'false').lower() == 'true'

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
                                    smaller_tokens.append(direct_data[0])
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

def should_send_alert(coin, rsi, grid_params):
    """
    Determine if this coin warrants an alert based on opportunity criteria
    """
    symbol = coin['symbol'].upper()
    
    # Strong RSI signals (oversold/overbought)
    if rsi <= RSI_OVERSOLD_THRESHOLD:
        logging.info(f"{symbol}: Strong oversold signal (RSI: {rsi:.1f})")
        return True, "OVERSOLD"
    
    if rsi >= RSI_OVERBOUGHT_THRESHOLD:
        logging.info(f"{symbol}: Strong overbought signal (RSI: {rsi:.1f})")
        return True, "OVERBOUGHT"
    
    # High volatility opportunities
    if grid_params['volatility'] > HIGH_VOLATILITY_THRESHOLD:
        logging.info(f"{symbol}: High volatility opportunity ({grid_params['volatility']:.1%})")
        return True, "HIGH_VOLATILITY"
    
    # High confidence directional bias (but not neutral)
    if (grid_params['direction_confidence'] == "High" and 
        grid_params['direction'] != "Neutral" and
        (rsi <= 35 or rsi >= 65)):  # Slightly less strict than extreme RSI
        logging.info(f"{symbol}: High confidence directional signal ({grid_params['direction']})")
        return True, "DIRECTIONAL"
    
    # Main tokens with medium signals (lower threshold for priority tokens)
    if coin['id'] in MAIN_TOKENS and (rsi <= 35 or rsi >= 65):
        logging.info(f"{symbol}: Main token with medium signal (RSI: {rsi:.1f})")
        return True, "MAIN_TOKEN"
    
    return False, None

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
        logging.info("Starting opportunity-based grid analysis...")
        market_data = fetch_market_data()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        current_hour = datetime.now(timezone.utc).hour

        opportunities = []
        summary_data = []

        if not market_data:
            logging.info("No market data available")
            if FORCE_DAILY_SUMMARY:
                send_telegram(f"*📊 DAILY GRID SUMMARY — {ts}*\nNo market data available.")
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
            
            # Store for potential daily summary
            summary_data.append({
                'coin': coin,
                'rsi': rsi,
                'grid_params': grid_params,
                'symbol': symbol
            })
            
            # Check if this is an opportunity worth alerting
            should_alert, alert_type = should_send_alert(coin, rsi, grid_params)
            
            if should_alert:
                price_fmt = format_price(current_price)
                low_fmt = format_price(grid_params['min_price'])
                high_fmt = format_price(grid_params['max_price'])
                
                # Create alert with opportunity type
                alert_type_emoji = {
                    "OVERSOLD": "🟢💥",
                    "OVERBOUGHT": "🔴💥", 
                    "HIGH_VOLATILITY": "⚡🌪️",
                    "DIRECTIONAL": "🎯📈",
                    "MAIN_TOKEN": "🏆⭐"
                }
                
                confidence_emoji = "🔥" if grid_params['direction_confidence'] == "High" else "⚡"
                direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[grid_params['direction']]
                
                alert = f"{alert_type_emoji.get(alert_type, '🚨')} *OPPORTUNITY DETECTED*\n"
                alert += f"{direction_emoji} *{symbol}* RSI {rsi:.1f} | {grid_params['market_tier'].upper()}-CAP\n"
                alert += f"📊 *COMPLETE GRID SETUP*\n"
                alert += f"• Price Range: `{low_fmt} - {high_fmt}`\n"
                alert += f"• Grid Count: `{grid_params['grids']} grids`\n"
                alert += f"• Grid Mode: `{grid_params['mode']}`\n"
                alert += f"• Direction: `{grid_params['direction']}` {confidence_emoji}\n"
                alert += f"• Trailing: `{grid_params['trailing']}`\n"
                alert += f"• Stop Loss: `{grid_params['stop_loss']}`\n"
                alert += f"• Expected Cycles/Day: `~{grid_params['expected_daily_cycles']}`\n"
                alert += f"• Volatility: `{grid_params['volatility']:.1%}` ({grid_params['mode']} recommended)\n"
                
                # Add reasoning based on alert type
                if alert_type == "OVERSOLD":
                    reason = f"Extremely oversold conditions (RSI {rsi:.1f}). Strong rebound potential for Long grid."
                elif alert_type == "OVERBOUGHT":
                    reason = f"Extremely overbought conditions (RSI {rsi:.1f}). High decline potential for Short grid."
                elif alert_type == "HIGH_VOLATILITY":
                    reason = f"High volatility ({grid_params['volatility']:.1%}) creates excellent grid trading conditions."
                elif alert_type == "DIRECTIONAL":
                    reason = f"High confidence {grid_params['direction'].lower()} signal with favorable RSI positioning."
                elif alert_type == "MAIN_TOKEN":
                    reason = f"Priority token with favorable RSI positioning for grid trading."
                else:
                    reason = f"Multiple favorable conditions detected for grid trading setup."
                
                alert += f"\n💡 *Analysis*: {reason}"
                
                opportunities.append(alert)
                logging.info(f"Opportunity detected: {symbol} ({alert_type})")

        # Send alerts based on conditions
        if opportunities:
            # Send opportunity alerts
            message = f"*🚨 GRID TRADING OPPORTUNITIES — {ts}*\n\n"
            message += '\n\n'.join(opportunities[:3])  # Limit to 3 opportunities per message
            
            if len(opportunities) > 3:
                message += f"\n\n*📈 Additional Opportunities*: {len(opportunities) - 3} more detected. Check next update for details."
            
            logging.info(f"Sending {len(opportunities)} opportunity alerts")
            send_telegram(message)
            
        elif FORCE_DAILY_SUMMARY or current_hour == 8:  # Daily summary at 8 AM UTC
            # Send daily summary when no opportunities or forced
            top_3 = sorted(summary_data, key=lambda x: abs(x['rsi'] - 50), reverse=True)[:3]
            
            if top_3:
                message = f"*📊 DAILY GRID SUMMARY — {ts}*\n\n"
                message += "*🔍 Market Analysis*\n"
                message += f"• Scanned {len(summary_data)} tokens\n"
                message += f"• No immediate opportunities detected\n"
                message += f"• Market appears to be in consolidation phase\n\n"
                
                message += "*📈 Top RSI Positions*\n"
                for i, item in enumerate(top_3, 1):
                    direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[item['grid_params']['direction']]
                    message += f"{i}. {direction_emoji} *{item['symbol']}*: RSI {item['rsi']:.1f} ({item['grid_params']['volatility']:.1%} vol)\n"
                
                message += f"\n⏰ *Next Check*: Will alert when opportunities arise"
                
                logging.info("Sending daily summary")
                send_telegram(message)
            else:
                logging.info("No data available for daily summary")
        else:
            logging.info(f"No opportunities detected. Checked {len(summary_data)} tokens. Staying quiet.")

        logging.info("Opportunity-based analysis completed")

    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        send_telegram(f"⚠️ API Error: {e}")
    except Exception as e:
        logging.error(f"Unexpected Error: {e}")
        send_telegram(f"⚠️ Unexpected Error: {e}")

if __name__ == "__main__":
    main()
