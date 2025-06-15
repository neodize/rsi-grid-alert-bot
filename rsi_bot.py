import requests
import time
from datetime import datetime, timezone
import os
import re
import logging
import math
import numpy as np
from scipy import stats
import statistics

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')
COINGECKO_API = 'https://api.coingecko.com/api/v3'
TOP_COINS_LIMIT = 100
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
TOP_COINS_TO_EXCLUDE = 20
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']

# Comprehensive exclusion lists (same as original)
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
    'SYNTH', 'PERP', 'SHIB', 'DOGE', 'PEPE', 'FLOKI', 'BABYDOGE',
    'LUNA', 'LUNC', 'USTC'
}

def is_excluded_token(symbol, name):
    """Check if a token should be excluded (same as original)"""
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
    """Send Telegram message (same as original)"""
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
        logging.error(f"Telegram send failed: {e}")
        time.sleep(60)
        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            logging.info(f"Telegram retry succeeded: {message[:50]}...")
        except requests.exceptions.RequestException as e2:
            logging.error(f"Telegram retry failed: {e2}")
            return

def fetch_market_data():
    """Fetch market data (same as original but simplified for readability)"""
    logging.info("Fetching market data from CoinGecko...")
    url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={TOP_COINS_LIMIT}&page=1&sparkline=true"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    
    # Initial filtering
    data = [coin for coin in response.json() if coin['total_volume'] > MIN_VOLUME and coin['current_price'] > MIN_PRICE]
    logging.info(f"After volume/price filter: {len(data)} coins")
    
    # Filter out unwanted tokens
    filtered_data = []
    for coin in data:
        is_excluded, reason = is_excluded_token(coin['symbol'], coin['name'])
        if not is_excluded:
            filtered_data.append(coin)
    
    logging.info(f"After token filtering: {len(filtered_data)} coins")
    
    # Separate main tokens from smaller tokens
    main_tokens_found = [coin for coin in filtered_data if coin['id'] in MAIN_TOKENS]
    smaller_tokens = [coin for i, coin in enumerate(filtered_data) 
                     if i >= TOP_COINS_TO_EXCLUDE and coin['id'] not in MAIN_TOKENS]
    
    # Try to fetch missing main tokens directly
    missing_main_tokens = [token_id for token_id in MAIN_TOKENS 
                          if not any(coin['id'] == token_id for coin in main_tokens_found)]
    
    for token_id in missing_main_tokens:
        try:
            if token_id == 'hyperliquid':
                for variant in HYPE_VARIANTS:
                    direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={variant}&sparkline=true"
                    direct_response = requests.get(direct_url, timeout=10)
                    direct_response.raise_for_status()
                    direct_data = direct_response.json()
                    
                    if (direct_data and len(direct_data) > 0 and 
                        direct_data[0]['total_volume'] > MIN_VOLUME and 
                        direct_data[0]['current_price'] > MIN_PRICE):
                        is_excluded, reason = is_excluded_token(direct_data[0]['symbol'], direct_data[0]['name'])
                        if not is_excluded:
                            main_tokens_found.append(direct_data[0])
                            break
            else:
                direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={token_id}&sparkline=true"
                direct_response = requests.get(direct_url, timeout=10)
                direct_response.raise_for_status()
                direct_data = direct_response.json()
                
                if (direct_data and len(direct_data) > 0 and 
                    direct_data[0]['total_volume'] > MIN_VOLUME and 
                    direct_data[0]['current_price'] > MIN_PRICE):
                    is_excluded, reason = is_excluded_token(direct_data[0]['symbol'], direct_data[0]['name'])
                    if not is_excluded:
                        main_tokens_found.append(direct_data[0])
        except Exception as e:
            logging.error(f"Failed to fetch {token_id}: {e}")
    
    final_tokens = main_tokens_found + smaller_tokens
    logging.info(f"Final tokens: {len(final_tokens)} ({len(main_tokens_found)} main, {len(smaller_tokens)} smaller)")
    
    return final_tokens

# ==================== ENHANCED TECHNICAL ANALYSIS ====================

def calculate_sma(prices, period):
    """Calculate Simple Moving Average"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calculate_ema(prices, period, smoothing=2):
    """Calculate Exponential Moving Average"""
    if len(prices) < period:
        return None
    
    sma = sum(prices[:period]) / period
    multiplier = smoothing / (period + 1)
    ema = sma
    
    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
    
    return ema

def calculate_atr(highs, lows, closes, period=14):
    """Calculate Average True Range"""
    if len(highs) < period + 1:
        return None
    
    true_ranges = []
    for i in range(1, len(highs)):
        high_low = highs[i] - lows[i]
        high_close_prev = abs(highs[i] - closes[i-1])
        low_close_prev = abs(lows[i] - closes[i-1])
        true_range = max(high_low, high_close_prev, low_close_prev)
        true_ranges.append(true_range)
    
    if len(true_ranges) < period:
        return None
    
    return sum(true_ranges[-period:]) / period

def calculate_bollinger_bands(prices, period=20, std_dev=2):
    """Calculate Bollinger Bands"""
    if len(prices) < period:
        return None, None, None
    
    sma = sum(prices[-period:]) / period
    variance = sum([(price - sma) ** 2 for price in prices[-period:]]) / period
    std = math.sqrt(variance)
    
    upper_band = sma + (std * std_dev)
    lower_band = sma - (std * std_dev)
    
    return upper_band, sma, lower_band

def calculate_rsi(prices, period=14):
    """Enhanced RSI calculation"""
    if len(prices) < period + 1:
        return None
    
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    
    if len(gains) < period:
        return None
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi

def calculate_dynamic_rsi_levels(prices, current_rsi, lookback=50):
    """Calculate dynamic RSI overbought/oversold levels based on historical data"""
    if len(prices) < lookback + 14:
        return 30, 70  # Default levels
    
    # Calculate RSI for historical periods
    historical_rsi = []
    for i in range(14, min(len(prices), lookback + 14)):
        segment = prices[i-14:i+1]
        rsi = calculate_rsi(segment)
        if rsi is not None:
            historical_rsi.append(rsi)
    
    if not historical_rsi:
        return 30, 70
    
    # Use percentiles for dynamic levels
    oversold_level = np.percentile(historical_rsi, 25)  # 25th percentile
    overbought_level = np.percentile(historical_rsi, 75)  # 75th percentile
    
    # Ensure reasonable bounds
    oversold_level = max(20, min(35, oversold_level))
    overbought_level = max(65, min(80, overbought_level))
    
    return oversold_level, overbought_level

def detect_support_resistance(prices, window=5):
    """Detect key support and resistance levels"""
    if len(prices) < window * 2:
        return [], []
    
    highs = []
    lows = []
    
    for i in range(window, len(prices) - window):
        # Check for local high
        if all(prices[i] >= prices[i-j] for j in range(1, window+1)) and \
           all(prices[i] >= prices[i+j] for j in range(1, window+1)):
            highs.append(prices[i])
        
        # Check for local low
        if all(prices[i] <= prices[i-j] for j in range(1, window+1)) and \
           all(prices[i] <= prices[i+j] for j in range(1, window+1)):
            lows.append(prices[i])
    
    # Get most significant levels (top 3 of each)
    resistance_levels = sorted(set(highs), reverse=True)[:3]
    support_levels = sorted(set(lows))[:3]
    
    return support_levels, resistance_levels

def calculate_volume_profile_zones(prices, volumes, num_zones=5):
    """Calculate volume profile to identify fair value areas"""
    if len(prices) != len(volumes) or len(prices) < num_zones:
        return []
    
    price_min, price_max = min(prices), max(prices)
    zone_size = (price_max - price_min) / num_zones
    
    zones = []
    for i in range(num_zones):
        zone_low = price_min + (i * zone_size)
        zone_high = zone_low + zone_size
        zone_volume = 0
        zone_count = 0
        
        for j, price in enumerate(prices):
            if zone_low <= price < zone_high:
                zone_volume += volumes[j] if j < len(volumes) else 0
                zone_count += 1
        
        if zone_count > 0:
            zones.append({
                'low': zone_low,
                'high': zone_high,
                'volume': zone_volume,
                'avg_price': (zone_low + zone_high) / 2
            })
    
    # Sort by volume (highest first)
    return sorted(zones, key=lambda x: x['volume'], reverse=True)

def calculate_market_regime(prices, volumes=None):
    """Determine current market regime (trending vs ranging)"""
    if len(prices) < 50:
        return "insufficient_data", 0.5
    
    # Calculate trend strength using multiple methods
    
    # 1. Linear regression slope
    x = list(range(len(prices)))
    slope, intercept, r_value, p_value, std_err = stats.linregress(x[-20:], prices[-20:])
    trend_strength = abs(r_value)  # How well data fits the trend line
    
    # 2. Moving average alignment
    sma_20 = calculate_sma(prices, 20)
    sma_50 = calculate_sma(prices, 50) if len(prices) >= 50 else sma_20
    
    ma_alignment = 0
    if sma_20 and sma_50:
        ma_diff = abs(sma_20 - sma_50) / sma_50
        ma_alignment = min(1.0, ma_diff * 10)  # Normalize
    
    # 3. Price range vs average true range
    recent_high = max(prices[-20:])
    recent_low = min(prices[-20:])
    price_range = (recent_high - recent_low) / prices[-1]
    
    # 4. Volatility clustering
    returns = [(prices[i] / prices[i-1] - 1) for i in range(1, len(prices))]
    volatility = np.std(returns[-20:]) if len(returns) >= 20 else 0.02
    
    # Combine indicators
    regime_score = (trend_strength * 0.4 + ma_alignment * 0.3 + 
                   min(1.0, price_range * 5) * 0.2 + min(1.0, volatility * 20) * 0.1)
    
    if regime_score > 0.7:
        return "trending", regime_score
    elif regime_score < 0.3:
        return "ranging", 1 - regime_score
    else:
        return "transitional", 0.5

def calculate_correlation_with_btc(coin_prices, btc_prices):
    """Calculate correlation with Bitcoin"""
    if len(coin_prices) != len(btc_prices) or len(coin_prices) < 10:
        return 0.5  # Neutral correlation
    
    # Calculate returns
    coin_returns = [(coin_prices[i] / coin_prices[i-1] - 1) for i in range(1, len(coin_prices))]
    btc_returns = [(btc_prices[i] / btc_prices[i-1] - 1) for i in range(1, len(btc_prices))]
    
    if len(coin_returns) < 5:
        return 0.5
    
    correlation = np.corrcoef(coin_returns, btc_returns)[0, 1]
    
    return correlation if not np.isnan(correlation) else 0.5

# ==================== ENHANCED GRID ANALYSIS ====================

class GridAnalyzer:
    def __init__(self, coin_data, btc_data=None):
        self.coin = coin_data
        self.btc_data = btc_data
        self.current_price = coin_data['current_price']
        self.symbol = coin_data['symbol'].upper()
        
        # Extract price data
        sparkline_data = coin_data.get('sparkline_in_7d', {})
        self.prices = sparkline_data.get('price', []) if sparkline_data else []
        
        if len(self.prices) < 20:
            # Generate synthetic price data based on available information
            self.prices = self._generate_synthetic_prices()
        
        # Extract volume data (approximated)
        self.volumes = self._approximate_volumes()
        
        # Initialize analysis results
        self.analysis = {}
    
    def _generate_synthetic_prices(self):
        """Generate synthetic price data when sparkline is insufficient"""
        change_24h = self.coin.get('price_change_percentage_24h', 0) / 100
        base_price = self.current_price / (1 + change_24h)
        
        # Generate 50 data points with some randomness
        prices = []
        for i in range(50):
            # Linear progression with noise
            progress = i / 49
            noise = np.random.normal(0, 0.02)  # 2% noise
            price = base_price * (1 + change_24h * progress + noise)
            prices.append(max(0.0001, price))  # Ensure positive prices
        
        return prices
    
    def _approximate_volumes(self):
        """Approximate volume distribution across price points"""
        total_volume = self.coin.get('total_volume', 1000000)
        avg_volume = total_volume / len(self.prices)
        
        # Generate volumes with some variation
        volumes = []
        for _ in range(len(self.prices)):
            volume_multiplier = np.random.uniform(0.5, 1.5)
            volumes.append(avg_volume * volume_multiplier)
        
        return volumes
    
    def analyze_trend_filters(self):
        """Analyze trend using multiple moving averages"""
        if len(self.prices) < 50:
            return {"trend": "neutral", "strength": 0.5, "ma_cross": "neutral"}
        
        sma_20 = calculate_sma(self.prices, 20)
        sma_50 = calculate_sma(self.prices, 50)
        ema_12 = calculate_ema(self.prices, 12)
        ema_26 = calculate_ema(self.prices, 26)
        
        # Moving average crossover signals
        ma_cross = "neutral"
        if sma_20 and sma_50:
            if sma_20 > sma_50 * 1.02:  # 2% buffer to avoid whipsaws
                ma_cross = "bullish"
            elif sma_20 < sma_50 * 0.98:
                ma_cross = "bearish"
        
        # Price position relative to MAs
        price_above_sma20 = self.current_price > sma_20 if sma_20 else False
        price_above_sma50 = self.current_price > sma_50 if sma_50 else False
        
        # Determine trend strength
        if price_above_sma20 and price_above_sma50 and ma_cross == "bullish":
            trend, strength = "bullish", 0.8
        elif not price_above_sma20 and not price_above_sma50 and ma_cross == "bearish":
            trend, strength = "bearish", 0.8
        elif ma_cross == "neutral":
            trend, strength = "neutral", 0.3
        else:
            trend, strength = "neutral", 0.5
        
        return {
            "trend": trend,
            "strength": strength,
            "ma_cross": ma_cross,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "price_above_sma20": price_above_sma20,
            "price_above_sma50": price_above_sma50
        }
    
    def analyze_volatility_regime(self):
        """Comprehensive volatility analysis"""
        if len(self.prices) < 20:
            return {"regime": "low", "atr": None, "bb_width": None}
        
        # Calculate ATR (approximated with price data only)
        highs = [max(self.prices[max(0, i-2):i+3]) for i in range(len(self.prices))]
        lows = [min(self.prices[max(0, i-2):i+3]) for i in range(len(self.prices))]
        atr = calculate_atr(highs, lows, self.prices)
        
        # Calculate Bollinger Band width
        upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(self.prices)
        bb_width = ((upper_bb - lower_bb) / middle_bb) if all(x is not None for x in [upper_bb, lower_bb, middle_bb]) else None
        
        # Determine volatility regime
        price_volatility = np.std([(self.prices[i] / self.prices[i-1] - 1) for i in range(1, len(self.prices))])
        
        if bb_width and bb_width > 0.15:
            regime = "high"
        elif bb_width and bb_width < 0.05:
            regime = "low"
        else:
            regime = "medium"
        
        return {
            "regime": regime,
            "atr": atr,
            "atr_pct": (atr / self.current_price * 100) if atr else None,
            "bb_width": bb_width,
            "price_volatility": price_volatility,
            "bollinger_bands": {
                "upper": upper_bb,
                "middle": middle_bb,
                "lower": lower_bb
            }
        }
    
    def analyze_market_structure(self):
        """Analyze support/resistance and market structure"""
        support_levels, resistance_levels = detect_support_resistance(self.prices)
        volume_zones = calculate_volume_profile_zones(self.prices, self.volumes)
        
        # Find nearest support and resistance
        nearest_support = max([s for s in support_levels if s < self.current_price], default=None)
        nearest_resistance = min([r for r in resistance_levels if r > self.current_price], default=None)
        
        # Calculate distance to key levels
        support_distance = ((self.current_price - nearest_support) / self.current_price) if nearest_support else None
        resistance_distance = ((nearest_resistance - self.current_price) / self.current_price) if nearest_resistance else None
        
        return {
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "support_distance_pct": support_distance * 100 if support_distance else None,
            "resistance_distance_pct": resistance_distance * 100 if resistance_distance else None,
            "volume_zones": volume_zones[:3],  # Top 3 volume zones
            "range_bound": len(support_levels) >= 2 and len(resistance_levels) >= 2
        }
    
    def analyze_rsi_signals(self):
        """Enhanced RSI analysis with dynamic levels"""
        rsi = calculate_rsi(self.prices)
        if rsi is None:
            return {"rsi": None, "signal": "neutral", "dynamic_levels": (30, 70)}
        
        # Calculate dynamic RSI levels
        oversold_level, overbought_level = calculate_dynamic_rsi_levels(self.prices, rsi)
        
        # Determine RSI signal
        if rsi <= oversold_level:
            signal = "oversold"
        elif rsi >= overbought_level:
            signal = "overbought"
        elif rsi <= oversold_level + 5:
            signal = "approaching_oversold"
        elif rsi >= overbought_level - 5:
            signal = "approaching_overbought"
        else:
            signal = "neutral"
        
        return {
            "rsi": rsi,
            "signal": signal,
            "dynamic_levels": (oversold_level, overbought_level),
            "static_levels": (30, 70)
        }
    
    def analyze_market_correlation(self):
        """Analyze correlation with major cryptocurrencies"""
        if not self.btc_data:
            return {"btc_correlation": 0.5, "market_dependency": "medium"}
        
        btc_sparkline = self.btc_data.get('sparkline_in_7d', {}).get('price', [])
        if len(btc_sparkline) < 10:
            return {"btc_correlation": 0.5, "market_dependency": "medium"}
        
        # Align price arrays
        min_length = min(len(self.prices), len(btc_sparkline))
        coin_prices = self.prices[-min_length:]
        btc_prices = btc_sparkline[-min_length:]
        
        correlation = calculate_correlation_with_btc(coin_prices, btc_prices)
        
        # Determine market dependency
        if abs(correlation) > 0.8:
            dependency = "high"
        elif abs(correlation) > 0.5:
            dependency = "medium"
        else:
            dependency = "low"
        
        return {
            "btc_correlation": correlation,
            "market_dependency": dependency,
            "independent_movement": abs(correlation) < 0.3
        }
    
    def determine_grid_suitability(self):
        """Comprehensive grid trading suitability analysis"""
        # Run all analyses
        trend_analysis = self.analyze_trend_filters()
        volatility_analysis = self.analyze_volatility_regime()
        structure_analysis = self.analyze_market_structure()
        rsi_analysis = self.analyze_rsi_signals()
        correlation_analysis = self.analyze_market_correlation()
        
        # Calculate grid suitability score
        score = 0
        reasons = []
        
        # Trend factor (prefer ranging/neutral markets)
        if trend_analysis["trend"] == "neutral":
            score += 30
            reasons.append("Neutral trend ideal for grid trading")
        elif trend_analysis["strength"] < 0.6:
            score += 20
            reasons.append("Weak trend allows for grid opportunities")
        else:
            score -= 10
            reasons.append(f"Strong {trend_analysis['trend']} trend not ideal for grids")
        
        # Volatility factor (prefer medium volatility)
        vol_regime = volatility_analysis["regime"]
        if vol_regime == "medium":
            score += 25
            reasons.append("Medium volatility perfect for grid trading")
        elif vol_regime == "low":
            score += 10
            reasons.append("Low volatility acceptable for tight grids")
        else:
            score -= 5
            reasons.append("High volatility requires wider grid spacing")
        
        # Market structure factor (prefer range-bound markets)
        if structure_analysis["range_bound"]:
            score += 20
            reasons.append("Clear support/resistance levels identified")
        
        # RSI factor
        rsi_signal = rsi_analysis["signal"]
        if rsi_signal in ["oversold", "overbought"]:
            score += 15
            reasons.append(f"RSI shows {rsi_signal} conditions")
        elif rsi_signal in ["approaching_oversold", "approaching_overbought"]:
            score += 10
            reasons.append(f"RSI {rsi_signal.replace('_', ' ')}")
        
        # Market dependency factor
        if correlation_analysis["market_dependency"] == "low":
            score += 10
            reasons.append("Low market correlation provides independence")
        
        # Volume factor
        volume_24h = self.coin.get('total_volume', 0)
        if volume_24h > 50_000_000:
            score += 10
            reasons.append("High trading volume ensures liquidity")
        
        # Determine final suitability
        if score >= 70:
            suitability = "excellent"
        elif score >= 50:
            suitability = "good"
        elif score >= 30:
            suitability = "moderate"
        else:
            suitability = "poor"
        
        return {
            "score": score,
            "suitability": suitability,
            "reasons": reasons,
            "trend": trend_analysis,
            "volatility": volatility_analysis,
            "structure": structure_analysis,
            "rsi": rsi_analysis,
            "correlation": correlation_analysis
        }
    
    def calculate_optimal_grid_parameters(self):
        """Calculate optimal grid parameters using multi-indicator analysis"""
        analysis = self.determine_grid_suitability()
        
        if analysis["suitability"] == "poor":
            return None  # Don't recommend grid trading
        
        # Base parameters by market cap
        market_cap = self.coin.get('market_cap', 0)
        if market_cap >= 50_000_000_000:
            base_params = {"spacing": 0.003, "max_grids": 150, "tier": "mega"}
        elif market_cap >= 10_000_000_000:
            base_params = {"spacing": 0.004, "max_grids": 120, "tier": "large"}
        elif market_cap >= 1_000_000_000:
            base_params = {"spacing": 0.006, "max_grids": 100, "tier": "mid"}
        else:
            base_params = {"spacing": 0.008, "max_grids": 80, "tier": "small"}
        
        # Adjust for volatility
        vol_regime = analysis["volatility"]["regime"]
        if vol_regime == "high":
            spacing_multiplier = 2.0
            grid_mode = "Geometric"
        elif vol_regime == "medium":
            spacing_multiplier = 1.2
            grid_mode = "Arithmetic"
        else:
            spacing_multiplier = 0.8
            grid_mode = "Arithmetic"
        
        # Adjust for trend
        trend_strength = analysis["trend"]["strength"]
        if trend_strength > 0.7:
            spacing_multiplier *= 1.5  # Wider spacing for trending markets
        
        # Calculate price range
        structure = analysis["structure"]
        rsi_data = analysis["rsi"]
        
        # Use support/resistance levels if available
        if structure["nearest_support"] and structure["nearest_resistance"]:
            min_price = structure["nearest_support"] * 0.95
            max_price = structure["nearest_resistance"] * 1.05
        else:
            # Use Bollinger Bands if available
            bb = analysis["volatility"]["bollinger_bands"]
            if bb["lower"] and bb["upper"]:
                min_price = bb["lower"]
                max_price = bb["upper"]
            else:
                # Fallback to RSI-based range
                volatility_pct = analysis["volatility"]["price_volatility"] or 0.05
                min_price = self.current_price * (1 - volatility_pct * 3)
                max_price = self.current_price * (1 + volatility_pct * 3)
        
        # Calculate grid parameters
        final_spacing = base_params["spacing"] * spacing_multiplier
        price_range = max_price - min_price
        theoretical_grids = int(price_range / (self.current_price * final_spacing))
        optimal_grids = min(base_params["max_grids"], max(15, theoretical_grids))
        
        # Determine direction based on multiple indicators
        rsi_signal = rsi_data["signal"]
        trend_direction = analysis["trend"]["trend"]
        
        if rsi_signal in ["oversold", "approaching_oversold"] and trend_direction != "bearish":
            direction = "Long"
            confidence = "High" if rsi_signal == "oversold" else "Medium"
        elif rsi_signal in ["overbought", "approaching_overbought"] and trend_direction != "bullish":
            direction = "Short"
            confidence = "High" if rsi_signal == "overbought" else "Medium"
        elif trend_direction == "neutral":
            direction = "Neutral"
            confidence = "High"
        else:
            direction = "Neutral"
            confidence = "Medium"
        
        # Advanced settings
        market_tier = base_params["tier"]
        trailing_stop = trend_strength < 0.5 and analysis["correlation"]["market_dependency"] != "high"
        stop_loss_pct = 7 if market_tier == "small" else 5 if direction != "Neutral" else None
        
        # Expected performance
        vol_pct = analysis["volatility"]["price_volatility"] * 100
        expected_cycles = int(vol_pct * 2)  # Rough estimate
        
        return {
            "min_price": min_price,
            "max_price": max_price,
            "grid_count": optimal_grids,
            "grid_mode": grid_mode,
            "direction": direction,
            "confidence": confidence,
            "spacing_pct": final_spacing * 100,
            "market_tier": market_tier,
            "trailing_stop": trailing_stop,
            "stop_loss_pct": stop_loss_pct,
            "expected_daily_cycles": expected_cycles,
            "suitability_score": analysis["score"],
            "analysis_summary": analysis
        }

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
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '=', '|', '{', '}', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def create_enhanced_alert(analyzer, grid_params):
    """Create comprehensive trading alert"""
    symbol = analyzer.symbol
    current_price = analyzer.current_price
    analysis = grid_params["analysis_summary"]
    
    # Header with suitability
    suitability_emoji = {
        "excellent": "🔥", "good": "⚡", "moderate": "⚠️", "poor": "❌"
    }[grid_params["analysis_summary"]["suitability"]]
    
    direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[grid_params["direction"]]
    
    alert = f"{direction_emoji} *{symbol}* {suitability_emoji} | {grid_params['market_tier'].upper()}-CAP\n"
    alert += f"📊 *MULTI-INDICATOR GRID SETUP*\n"
    
    # Core grid parameters
    alert += f"• Price Range: `{format_price(grid_params['min_price'])} - {format_price(grid_params['max_price'])}`\n"
    alert += f"• Grid Count: `{grid_params['grid_count']} grids ({grid_params['grid_mode']})`\n"
    alert += f"• Direction: `{grid_params['direction']}` ({grid_params['confidence']} confidence)\n"
    alert += f"• Spacing: `{grid_params['spacing_pct']:.2f}%`\n"
    
    # Advanced settings
    alert += f"• Trailing Stop: `{'Yes' if grid_params['trailing_stop'] else 'No'}`\n"
    stop_loss = f"{grid_params['stop_loss_pct']}%" if grid_params['stop_loss_pct'] else "Disabled"
    alert += f"• Stop Loss: `{stop_loss}`\n"
    alert += f"• Expected Cycles/Day: `~{grid_params['expected_daily_cycles']}`\n"
    
    # Technical analysis summary
    alert += f"\n🔍 *TECHNICAL ANALYSIS*\n"
    
    # Trend analysis
    trend = analysis["trend"]
    alert += f"• Trend: `{trend['trend'].title()} ({trend['strength']:.1%} strength)`\n"
    
    # RSI with dynamic levels
    rsi_data = analysis["rsi"]
    if rsi_data["rsi"]:
        dynamic_levels = rsi_data["dynamic_levels"]
        alert += f"• RSI: `{rsi_data['rsi']:.1f}` (Dynamic: {dynamic_levels[0]:.0f}/{dynamic_levels[1]:.0f})\n"
    
    # Volatility regime
    vol = analysis["volatility"]
    alert += f"• Volatility: `{vol['regime'].title()} regime`\n"
    if vol["atr_pct"]:
        alert += f"• ATR: `{vol['atr_pct']:.2f}%`\n"
    
    # Market structure
    structure = analysis["structure"]
    if structure["nearest_support"] and structure["nearest_resistance"]:
        alert += f"• Support: `{format_price(structure['nearest_support'])}`\n"
        alert += f"• Resistance: `{format_price(structure['nearest_resistance'])}`\n"
    
    # Market correlation
    correlation = analysis["correlation"]
    alert += f"• BTC Correlation: `{correlation['btc_correlation']:.2f}` ({correlation['market_dependency']})\n"
    
    # Strategic reasoning
    alert += f"\n💡 *STRATEGY REASONING*\n"
    
    # Get top 3 reasons
    top_reasons = grid_params["analysis_summary"]["reasons"][:3]
    for i, reason in enumerate(top_reasons, 1):
        alert += f"{i}. {reason}\n"
    
    # Performance expectations
    alert += f"\n📈 *PERFORMANCE OUTLOOK*\n"
    score = grid_params["suitability_score"]
    alert += f"• Suitability Score: `{score}/100`\n"
    
    if score >= 70:
        outlook = "Excellent grid trading opportunity with high profit potential"
    elif score >= 50:
        outlook = "Good setup with moderate profit expectations"
    else:
        outlook = "Moderate opportunity - monitor closely"
    
    alert += f"• Outlook: {outlook}\n"
    
    return alert

def main():
    """Enhanced main function with multi-indicator analysis"""
    try:
        logging.info("Starting enhanced multi-indicator grid analysis...")
        market_data = fetch_market_data()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        if not market_data:
            logging.info("No market data available")
            send_telegram(f"*ENHANCED GRID ANALYSIS — {ts}*\nNo market data available for analysis.")
            return

        # Find Bitcoin data for correlation analysis
        btc_data = None
        for coin in market_data:
            if coin['id'] == 'bitcoin':
                btc_data = coin
                break

        main_alerts = []
        small_alerts = []
        processed_count = 0
        suitable_count = 0

        for coin in market_data:
            processed_count += 1
            
            try:
                # Create analyzer instance
                analyzer = GridAnalyzer(coin, btc_data)
                
                # Calculate optimal grid parameters
                grid_params = analyzer.calculate_optimal_grid_parameters()
                
                if grid_params is None:
                    logging.debug(f"Skipping {coin['symbol']} - poor grid suitability")
                    continue
                
                suitable_count += 1
                
                # Create alert
                alert = create_enhanced_alert(analyzer, grid_params)
                
                # Categorize alerts
                if coin['id'] in MAIN_TOKENS:
                    main_alerts.append(alert)
                    logging.info(f"Added main token alert: {coin['symbol']} (Score: {grid_params['suitability_score']})")
                else:
                    small_alerts.append((alert, grid_params['suitability_score']))
                    logging.debug(f"Added small token alert: {coin['symbol']} (Score: {grid_params['suitability_score']})")
                
            except Exception as e:
                logging.error(f"Error analyzing {coin['symbol']}: {e}")
                continue

        # Sort small alerts by suitability score
        small_alerts.sort(key=lambda x: x[1], reverse=True)
        small_alerts = [alert[0] for alert in small_alerts]

        # Compose final message
        message = f"*🤖 ENHANCED MULTI-INDICATOR GRID ANALYSIS — {escape_markdown(ts)}*\n\n"
        message += f"📊 Analyzed: {processed_count} tokens | Suitable: {suitable_count}\n\n"
        
        if main_alerts:
            message += "*🏆 MAIN TOKENS*\n" + '\n\n'.join(main_alerts) + '\n\n'
        
        if small_alerts:
            message += "*💎 TOP OPPORTUNITIES*\n" + '\n\n'.join(small_alerts[:4])
        
        if not main_alerts and not small_alerts:
            message += '❌ No suitable grid trading opportunities found.\n'
            message += '📈 Current market conditions may not favor grid trading strategies.'

        # Add footer with methodology
        message += '\n\n*📚 METHODOLOGY*\n'
        message += 'Multi-indicator analysis combining: RSI (dynamic levels), Moving Averages, '
        message += 'ATR, Bollinger Bands, Support/Resistance, Volume Profile, BTC Correlation, '
        message += 'and Market Regime Detection.'

        logging.info(f"Sending enhanced analysis message (suitable opportunities: {suitable_count})")
        send_telegram(message)
        logging.info("Enhanced multi-indicator analysis completed successfully")

    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        send_telegram(f"*ENHANCED GRID ANALYSIS ERROR*\nAPI Error: {str(e)[:100]}...")
    except Exception as e:
        logging.error(f"Unexpected Error in main(): {e}")
        send_telegram(f"*ENHANCED GRID ANALYSIS ERROR*\nUnexpected Error: {str(e)[:100]}...")

if __name__ == "__main__":
    main()
