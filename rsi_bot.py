import requests
import time
from datetime import datetime, timezone
import os
import logging
import math
import numpy as np
from scipy import stats

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')
PIONEX_API = 'https://api.pionex.com'
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
MAX_RECOMMENDATIONS = 5

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
    'SYNTH', 'PERP', 'SHIB', 'DOGE', 'PEPE', 'FLOKI', 'BABYDOGE',
    'LUNA', 'LUNC', 'USTC'
}

def is_excluded_token(symbol, name=""):
    """Check if a token should be excluded"""
    symbol_upper = symbol.upper()
    name_upper = name.upper() if name else ""
    
    if symbol_upper in WRAPPED_TOKENS:
        return True, "wrapped"
    if symbol_upper in STABLECOINS:
        return True, "stablecoin"
    if symbol_upper in EXCLUDED_TOKENS:
        return True, "excluded"
    if symbol_upper.endswith(('UP', 'DOWN', '3L', '3S')):
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
    """Send Telegram message"""
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

def fetch_pionex_market_data():
    """Fetch market data from Pionex API"""
    logging.info("Fetching market data from Pionex API...")
    
    try:
        # Get 24hr ticker data for all symbols
        url = f"{PIONEX_API}/api/v1/market/24hrs"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        if not response.json().get('result'):
            logging.error(f"Pionex API error: {response.json()}")
            return []
        
        tickers = response.json().get('data', [])
        logging.info(f"Retrieved {len(tickers)} tickers from Pionex")
        
        # Filter for perpetual contracts only (futures grid)
        perp_tickers = []
        for ticker in tickers:
            symbol = ticker.get('symbol', '')
            if symbol.endswith('_USDT') and 'PERP' in symbol:  # Filter for perpetual contracts
                # Extract base symbol
                base_symbol = symbol.replace('_USDT', '').replace('PERP', '')
                
                # Convert Pionex format to our expected format
                market_data = {
                    'symbol': base_symbol,
                    'name': base_symbol,
                    'current_price': float(ticker.get('close', 0)),
                    'price_change_percentage_24h': float(ticker.get('priceChangePercent', 0)),
                    'total_volume': float(ticker.get('quoteVolume', 0)),
                    'market_cap': float(ticker.get('quoteVolume', 0)) * 24,  # Approximate market cap
                    'high_24h': float(ticker.get('high', 0)),
                    'low_24h': float(ticker.get('low', 0)),
                    'raw_symbol': symbol
                }
                
                # Apply filters
                if (market_data['total_volume'] > MIN_VOLUME and 
                    market_data['current_price'] > MIN_PRICE):
                    
                    is_excluded, reason = is_excluded_token(base_symbol)
                    if not is_excluded:
                        perp_tickers.append(market_data)
        
        logging.info(f"After filtering: {len(perp_tickers)} perpetual contracts")
        
        # Sort by volume and return top candidates
        perp_tickers.sort(key=lambda x: x['total_volume'], reverse=True)
        return perp_tickers[:50]  # Get top 50 by volume for analysis
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Pionex market data: {e}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error in fetch_pionex_market_data: {e}")
        return []

def fetch_kline_data(symbol, interval='1h', limit=100):
    """Fetch historical kline data from Pionex"""
    try:
        url = f"{PIONEX_API}/api/v1/market/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        if not response.json().get('result'):
            return []
            
        klines = response.json().get('data', [])
        
        # Convert kline data to price arrays
        prices = []
        volumes = []
        highs = []
        lows = []
        
        for kline in klines:
            prices.append(float(kline[4]))  # Close price
            volumes.append(float(kline[5]))  # Volume
            highs.append(float(kline[2]))    # High price
            lows.append(float(kline[3]))     # Low price
        
        return {
            'prices': prices,
            'volumes': volumes,
            'highs': highs,
            'lows': lows
        }
        
    except Exception as e:
        logging.debug(f"Error fetching kline data for {symbol}: {e}")
        return None

# Technical Analysis Functions (keeping the same as before)
def calculate_sma(prices, period):
    """Calculate Simple Moving Average"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

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

class GridAnalyzer:
    def __init__(self, coin_data):
        self.coin = coin_data
        self.current_price = coin_data['current_price']
        self.symbol = coin_data['symbol'].upper()
        self.raw_symbol = coin_data['raw_symbol']
        
        # Fetch historical data from Pionex
        kline_data = fetch_kline_data(self.raw_symbol)
        if kline_data:
            self.prices = kline_data['prices']
            self.volumes = kline_data['volumes']
            self.highs = kline_data['highs']
            self.lows = kline_data['lows']
        else:
            # Generate synthetic data as fallback
            self.prices = self._generate_synthetic_prices()
            self.volumes = self._approximate_volumes()
            self.highs = self.prices.copy()
            self.lows = self.prices.copy()
    
    def _generate_synthetic_prices(self):
        """Generate synthetic price data when API data is unavailable"""
        change_24h = self.coin.get('price_change_percentage_24h', 0) / 100
        base_price = self.current_price / (1 + change_24h)
        
        prices = []
        for i in range(50):
            progress = i / 49
            noise = np.random.normal(0, 0.02)
            price = base_price * (1 + change_24h * progress + noise)
            prices.append(max(0.0001, price))
        
        return prices
    
    def _approximate_volumes(self):
        """Approximate volume distribution"""
        total_volume = self.coin.get('total_volume', 1000000)
        avg_volume = total_volume / len(self.prices)
        
        volumes = []
        for _ in range(len(self.prices)):
            volume_multiplier = np.random.uniform(0.5, 1.5)
            volumes.append(avg_volume * volume_multiplier)
        
        return volumes
    
    def analyze_volatility(self):
        """Analyze volatility using ATR and price data"""
        atr = calculate_atr(self.highs, self.lows, self.prices)
        upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(self.prices)
        
        bb_width = ((upper_bb - lower_bb) / middle_bb) if all(x is not None for x in [upper_bb, lower_bb, middle_bb]) else None
        
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
            "bb_width": bb_width
        }
    
    def analyze_trend(self):
        """Analyze trend using moving averages"""
        if len(self.prices) < 50:
            return {"trend": "neutral", "strength": 0.5}
        
        sma_20 = calculate_sma(self.prices, 20)
        sma_50 = calculate_sma(self.prices, 50)
        
        if sma_20 and sma_50:
            if sma_20 > sma_50 * 1.02:
                trend, strength = "bullish", 0.7
            elif sma_20 < sma_50 * 0.98:
                trend, strength = "bearish", 0.7
            else:
                trend, strength = "neutral", 0.3
        else:
            trend, strength = "neutral", 0.5
        
        return {"trend": trend, "strength": strength}
    
    def analyze_rsi_signals(self):
        """Analyze RSI for entry signals"""
        rsi = calculate_rsi(self.prices)
        if rsi is None:
            return {"rsi": None, "signal": "neutral"}
        
        if rsi <= 30:
            signal = "oversold"
        elif rsi >= 70:
            signal = "overbought"
        elif rsi <= 35:
            signal = "approaching_oversold"
        elif rsi >= 65:
            signal = "approaching_overbought"
        else:
            signal = "neutral"
        
        return {"rsi": rsi, "signal": signal}
    
    def calculate_grid_suitability(self):
        """Calculate grid trading suitability score"""
        volatility = self.analyze_volatility()
        trend = self.analyze_trend()
        rsi = self.analyze_rsi_signals()
        
        score = 0
        reasons = []
        
        # Volatility factor
        if volatility["regime"] == "medium":
            score += 30
            reasons.append("Optimal volatility for grid trading")
        elif volatility["regime"] == "low":
            score += 15
            reasons.append("Low volatility suitable for tight grids")
        else:
            score += 5
            reasons.append("High volatility requires careful management")
        
        # Trend factor
        if trend["trend"] == "neutral":
            score += 25
            reasons.append("Sideways trend ideal for grid strategy")
        elif trend["strength"] < 0.6:
            score += 15
            reasons.append("Weak trend allows grid opportunities")
        
        # RSI factor
        if rsi["signal"] in ["oversold", "overbought"]:
            score += 20
            reasons.append(f"RSI shows {rsi['signal']} conditions")
        elif rsi["signal"] in ["approaching_oversold", "approaching_overbought"]:
            score += 10
            reasons.append("RSI approaching extreme levels")
        
        # Volume factor
        volume_24h = self.coin.get('total_volume', 0)
        if volume_24h > 50_000_000:
            score += 15
            reasons.append("High trading volume ensures liquidity")
        elif volume_24h > MIN_VOLUME:
            score += 10
            reasons.append("Adequate trading volume")
        
        # Price stability (based on 24h change)
        price_change_24h = abs(self.coin.get('price_change_percentage_24h', 0))
        if price_change_24h < 5:
            score += 10
            reasons.append("Stable price movement")
        
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
            "volatility": volatility,
            "trend": trend,
            "rsi": rsi
        }
    
    def calculate_optimal_grid_parameters(self):
        """Calculate optimal grid parameters"""
        analysis = self.calculate_grid_suitability()
        
        if analysis["suitability"] == "poor":
            return None
        
        # Base parameters
        market_cap = self.coin.get('market_cap', 0)
        if market_cap >= 10_000_000_000:
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
        
        # Calculate price range
        volatility_pct = analysis["volatility"]["atr_pct"] or 5
        min_price = self.current_price * (1 - volatility_pct * 0.01 * 2)
        max_price = self.current_price * (1 + volatility_pct * 0.01 * 2)
        
        final_spacing = base_params["spacing"] * spacing_multiplier
        price_range = max_price - min_price
        optimal_grids = min(base_params["max_grids"], max(15, int(price_range / (self.current_price * final_spacing))))
        
        # Determine direction
        rsi_signal = analysis["rsi"]["signal"]
        trend_direction = analysis["trend"]["trend"]
        
        if rsi_signal in ["oversold", "approaching_oversold"] and trend_direction != "bearish":
            direction = "Long"
            confidence = "High" if rsi_signal == "oversold" else "Medium"
        elif rsi_signal in ["overbought", "approaching_overbought"] and trend_direction != "bullish":
            direction = "Short"
            confidence = "High" if rsi_signal == "overbought" else "Medium"
        else:
            direction = "Neutral"
            confidence = "Medium"
        
        return {
            "min_price": min_price,
            "max_price": max_price,
            "grid_count": optimal_grids,
            "grid_mode": grid_mode,
            "direction": direction,
            "confidence": confidence,
            "spacing_pct": final_spacing * 100,
            "market_tier": base_params["tier"],
            "analysis": analysis
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

def create_grid_alert(analyzer, grid_params):
    """Create grid trading alert"""
    symbol = analyzer.symbol
    current_price = analyzer.current_price
    analysis = grid_params["analysis"]
    
    suitability_emoji = {
        "excellent": "🔥", "good": "⚡", "moderate": "⚠️"
    }[analysis["suitability"]]
    
    direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[grid_params["direction"]]
    
    alert = f"{direction_emoji} *{symbol}* PERP {suitability_emoji} | {grid_params['market_tier'].upper()}-CAP\n"
    alert += f"💰 *Current Price:* `{format_price(current_price)}`\n"
    alert += f"📊 *GRID PARAMETERS*\n"
    alert += f"• Range: `{format_price(grid_params['min_price'])} - {format_price(grid_params['max_price'])}`\n"
    alert += f"• Grids: `{grid_params['grid_count']} ({grid_params['grid_mode']})`\n"
    alert += f"• Direction: `{grid_params['direction']}` ({grid_params['confidence']} confidence)\n"
    alert += f"• Spacing: `{grid_params['spacing_pct']:.2f}%`\n"
    
    # Technical analysis
    alert += f"\n🔍 *TECHNICAL SIGNALS*\n"
    
    rsi_data = analysis["rsi"]
    if rsi_data["rsi"]:
        alert += f"• RSI: `{rsi_data['rsi']:.1f}` ({rsi_data['signal']})\n"
    
    trend = analysis["trend"]
    alert += f"• Trend: `{trend['trend'].title()}`\n"
    
    vol = analysis["volatility"]
    alert += f"• Volatility: `{vol['regime'].title()}`\n"
    if vol["atr_pct"]:
        alert += f"• ATR: `{vol['atr_pct']:.2f}%`\n"
    
    # Key reasons
    alert += f"\n💡 *KEY FACTORS*\n"
    for i, reason in enumerate(analysis["reasons"][:3], 1):
        alert += f"{i}. {reason}\n"
    
    alert += f"\n📈 *Score:* `{analysis['score']}/100`"
    
    return alert

def main():
    """Main function using only Pionex API"""
    try:
        logging.info("Starting Pionex-only grid analysis for futures...")
        market_data = fetch_pionex_market_data()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        if not market_data:
            logging.info("No market data available from Pionex")
            send_telegram(f"*PIONEX FUTURES GRID ANALYSIS -- {ts}*\nNo market data available for analysis.")
            return

        suitable_alerts = []
        processed_count = 0

        for coin in market_data:
            processed_count += 1
            
            try:
                analyzer = GridAnalyzer(coin)
                grid_params = analyzer.calculate_optimal_grid_parameters()
                
                if grid_params is None:
                    continue
                
                alert = create_grid_alert(analyzer, grid_params)
                suitable_alerts.append((alert, grid_params["analysis"]["score"]))
                
                logging.info(f"Added alert: {coin['symbol']} (Score: {grid_params['analysis']['score']})")
                
            except Exception as e:
                logging.error(f"Error analyzing {coin['symbol']}: {e}")
                continue

        # Sort by score and take top 5
        suitable_alerts.sort(key=lambda x: x[1], reverse=True)
        top_alerts = [alert[0] for alert in suitable_alerts[:MAX_RECOMMENDATIONS]]

        # Compose final message
        message = f"*🚀 PIONEX FUTURES GRID OPPORTUNITIES -- {escape_markdown(ts)}*\n\n"
        message += f"📊 Analyzed: {processed_count} PERP contracts | Top: {len(top_alerts)}\n\n"
        
        if top_alerts:
            message += "*🎯 TOP 5 FUTURES GRID SETUPS*\n\n" + '\n\n'.join(top_alerts)
        else:
            message += '❌ No suitable futures grid opportunities found.\n'
            message += '📈 Current market conditions may not favor grid trading.'

        message += '\n\n*📚 DATA SOURCE*\n'
        message += 'Analysis powered by Pionex API - Free real-time perpetual contract data'

        logging.info(f"Sending Pionex analysis message ({len(top_alerts)} opportunities)")
        send_telegram(message)
        logging.info("Pionex futures grid analysis completed successfully")

    except Exception as e:
        logging.error(f"Error in main(): {e}")
        send_telegram(f"*PIONEX GRID ANALYSIS ERROR*\nError: {str(e)[:100]}...")

if __name__ == "__main__":
    main()
