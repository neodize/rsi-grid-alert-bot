import requests
import time
from datetime import datetime, timezone, timedelta
import os
import re
import logging
import math
import statistics
from typing import Dict, List, Tuple, Optional
import json

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')
COINGECKO_API = 'https://api.coingecko.com/api/v3'

# Enhanced Configuration
TOP_COINS_LIMIT = 100
MIN_VOLUME = 10_000_000
MIN_PRICE = 0.01
TOP_COINS_TO_EXCLUDE = 15
MAIN_TOKENS = ['bitcoin', 'ethereum', 'solana', 'hyperliquid']
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']

# Risk Management Settings
MAX_CORRELATION = 0.7  # Don't recommend highly correlated assets
MIN_PROFIT_POTENTIAL = 0.02  # Minimum 2% potential profit
PORTFOLIO_RISK_LIMIT = 0.25  # Max 25% of portfolio in grids
MIN_RANGE_EFFICIENCY = 0.15  # Grid range should be at least 15% of current price

class MarketRegime:
    """Detect current market conditions"""
    
    @staticmethod
    def analyze_trend(prices: List[float], volumes: List[float] = None) -> Dict:
        """Analyze trend strength and direction"""
        if len(prices) < 20:
            return {"trend": "unknown", "strength": 0, "suitable_for_grid": False}
        
        # Calculate multiple moving averages
        ma_5 = sum(prices[-5:]) / 5
        ma_10 = sum(prices[-10:]) / 10
        ma_20 = sum(prices[-20:]) / 20
        current = prices[-1]
        
        # Trend direction
        trend_score = 0
        if ma_5 > ma_10 > ma_20:
            trend_score += 2
        elif ma_5 > ma_10:
            trend_score += 1
        elif ma_5 < ma_10 < ma_20:
            trend_score -= 2
        elif ma_5 < ma_10:
            trend_score -= 1
            
        # Price vs MA alignment
        if current > ma_5 > ma_10:
            trend_score += 1
        elif current < ma_5 < ma_10:
            trend_score -= 1
            
        # Calculate trend strength (0-1)
        price_changes = [abs((prices[i] - prices[i-1]) / prices[i-1]) for i in range(1, len(prices))]
        avg_change = sum(price_changes) / len(price_changes)
        
        # Determine trend classification
        if trend_score >= 3:
            trend = "strong_uptrend"
            suitable = False  # Strong trends are bad for grids
        elif trend_score >= 1:
            trend = "weak_uptrend"
            suitable = True
        elif trend_score <= -3:
            trend = "strong_downtrend"
            suitable = False
        elif trend_score <= -1:
            trend = "weak_downtrend"
            suitable = True
        else:
            trend = "sideways"
            suitable = True
            
        return {
            "trend": trend,
            "strength": abs(trend_score) / 4,
            "suitable_for_grid": suitable,
            "avg_volatility": avg_change,
            "trend_score": trend_score
        }
    
    @staticmethod
    def detect_support_resistance(prices: List[float]) -> Dict:
        """Find key support and resistance levels"""
        if len(prices) < 50:
            return {"support": min(prices), "resistance": max(prices), "strength": 0}
            
        # Simple pivot detection
        pivots_high = []
        pivots_low = []
        
        for i in range(2, len(prices) - 2):
            # High pivot
            if prices[i] > prices[i-1] and prices[i] > prices[i+1] and prices[i] > prices[i-2] and prices[i] > prices[i+2]:
                pivots_high.append(prices[i])
            # Low pivot  
            if prices[i] < prices[i-1] and prices[i] < prices[i+1] and prices[i] < prices[i-2] and prices[i] < prices[i+2]:
                pivots_low.append(prices[i])
        
        # Find most relevant levels
        current = prices[-1]
        
        # Support: highest low below current price
        valid_supports = [p for p in pivots_low if p < current]
        support = max(valid_supports) if valid_supports else min(prices)
        
        # Resistance: lowest high above current price
        valid_resistances = [p for p in pivots_high if p > current]
        resistance = min(valid_resistances) if valid_resistances else max(prices)
        
        return {
            "support": support,
            "resistance": resistance,
            "strength": len(pivots_high) + len(pivots_low),
            "support_distance": (current - support) / current if support < current else 0,
            "resistance_distance": (resistance - current) / current if resistance > current else 0
        }

class EnhancedIndicators:
    """Enhanced technical indicators for better grid decisions"""
    
    @staticmethod
    def bollinger_bands(prices: List[float], period: int = 20, std_dev: int = 2) -> Dict:
        """Calculate Bollinger Bands"""
        if len(prices) < period:
            return None
            
        recent_prices = prices[-period:]
        ma = sum(recent_prices) / len(recent_prices)
        variance = sum([(p - ma) ** 2 for p in recent_prices]) / len(recent_prices)
        std = math.sqrt(variance)
        
        upper = ma + (std * std_dev)
        lower = ma - (std * std_dev)
        current = prices[-1]
        
        # Calculate position within bands
        bb_position = (current - lower) / (upper - lower) if upper > lower else 0.5
        
        return {
            "upper": upper,
            "middle": ma,
            "lower": lower,
            "position": bb_position,  # 0 = at lower band, 1 = at upper band
            "squeeze": (upper - lower) / ma < 0.1  # Bollinger Band squeeze
        }
    
    @staticmethod
    def rsi_enhanced(prices: List[float], period: int = 14) -> Optional[Dict]:
        """Enhanced RSI with divergence detection"""
        if len(prices) < period + 5:
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
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        # Detect oversold/overbought conditions
        oversold = rsi < 30
        overbought = rsi > 70
        
        # Calculate RSI momentum
        if len(prices) >= period * 2:
            prev_gains = []
            prev_losses = []
            for i in range(period + 1, period * 2 + 1):
                delta = prices[-i] - prices[-(i + 1)]
                prev_gains.append(max(0, delta))
                prev_losses.append(max(0, -delta))
            
            prev_avg_gain = sum(prev_gains) / period
            prev_avg_loss = sum(prev_losses) / period
            
            if prev_avg_loss == 0:
                prev_rsi = 100
            else:
                prev_rs = prev_avg_gain / prev_avg_loss
                prev_rsi = 100 - (100 / (1 + prev_rs))
            
            rsi_momentum = rsi - prev_rsi
        else:
            rsi_momentum = 0
        
        return {
            "value": rsi,
            "oversold": oversold,
            "overbought": overbought,
            "momentum": rsi_momentum,
            "signal_strength": abs(rsi - 50) / 50  # How far from neutral
        }
    
    @staticmethod
    def volume_analysis(volumes: List[float], prices: List[float]) -> Dict:
        """Analyze volume patterns"""
        if len(volumes) < 10:
            return {"trend": "unknown", "strength": 0}
            
        avg_volume = sum(volumes[-10:]) / 10
        current_volume = volumes[-1]
        
        # Volume trend
        recent_volumes = volumes[-5:]
        older_volumes = volumes[-10:-5]
        
        volume_trend = sum(recent_volumes) / len(recent_volumes) - sum(older_volumes) / len(older_volumes)
        volume_trend_pct = volume_trend / (sum(older_volumes) / len(older_volumes))
        
        # Price-volume relationship
        price_changes = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(-5, 0)]
        volume_changes = [(volumes[i] - volumes[i-1]) / volumes[i-1] for i in range(-5, 0)]
        
        # Simple correlation approximation
        pv_correlation = 0
        for p, v in zip(price_changes, volume_changes):
            if (p > 0 and v > 0) or (p < 0 and v < 0):
                pv_correlation += 1
            else:
                pv_correlation -= 1
        
        pv_correlation = pv_correlation / len(price_changes)
        
        return {
            "current_vs_avg": current_volume / avg_volume,
            "trend_pct": volume_trend_pct,
            "price_volume_correlation": pv_correlation,
            "high_volume": current_volume > avg_volume * 1.5
        }

class RiskManager:
    """Enhanced risk management and portfolio considerations"""
    
    @staticmethod
    def calculate_correlation(prices1: List[float], prices2: List[float]) -> float:
        """Calculate price correlation between two assets"""
        if len(prices1) < 10 or len(prices2) < 10:
            return 0
            
        min_len = min(len(prices1), len(prices2))
        p1 = prices1[-min_len:]
        p2 = prices2[-min_len:]
        
        # Calculate returns
        returns1 = [(p1[i] - p1[i-1]) / p1[i-1] for i in range(1, len(p1))]
        returns2 = [(p2[i] - p2[i-1]) / p2[i-1] for i in range(1, len(p2))]
        
        if len(returns1) < 5:
            return 0
            
        # Simple correlation calculation
        mean1 = sum(returns1) / len(returns1)
        mean2 = sum(returns2) / len(returns2)
        
        numerator = sum([(r1 - mean1) * (r2 - mean2) for r1, r2 in zip(returns1, returns2)])
        
        sum_sq1 = sum([(r1 - mean1) ** 2 for r1 in returns1])
        sum_sq2 = sum([(r2 - mean2) ** 2 for r2 in returns2])
        
        if sum_sq1 == 0 or sum_sq2 == 0:
            return 0
            
        denominator = math.sqrt(sum_sq1 * sum_sq2)
        
        return numerator / denominator if denominator != 0 else 0
    
    @staticmethod
    def estimate_grid_profit_potential(min_price: float, max_price: float, 
                                     current_price: float, grids: int, 
                                     volatility: float) -> Dict:
        """Estimate potential profit from grid setup"""
        if grids <= 1:
            return {"potential_profit": 0, "daily_trades": 0, "risk_score": 1}
            
        grid_spacing = (max_price - min_price) / (grids - 1)
        grid_spacing_pct = grid_spacing / current_price
        
        # Estimate trades per day based on volatility and grid spacing
        daily_volatility = volatility
        trades_per_day = (daily_volatility / grid_spacing_pct) * 0.5  # Conservative estimate
        
        # Profit per trade (simplified)
        profit_per_trade = grid_spacing_pct * 0.5  # Assuming 50% capture rate
        
        # Daily profit estimate
        daily_profit = trades_per_day * profit_per_trade
        
        # Risk assessment
        range_pct = (max_price - min_price) / current_price
        risk_score = 1 - min(range_pct / 0.5, 1)  # Lower risk for wider ranges
        
        return {
            "potential_daily_profit": daily_profit,
            "estimated_trades_per_day": trades_per_day,
            "grid_spacing_pct": grid_spacing_pct,
            "range_efficiency": range_pct,
            "risk_score": risk_score
        }

def send_telegram(message):
    """Enhanced Telegram sending with better error handling"""
    token_source = "GitHub Secrets" if os.getenv('TELEGRAM_TOKEN') else "fallback"
    chat_id_source = "GitHub Secrets" if os.getenv('TELEGRAM_CHAT_ID') else "fallback"
    logging.info(f"Sending Telegram message using token from {token_source} and chat_id from {chat_id_source}")

    if not TELEGRAM_TOKEN.strip() or not TELEGRAM_CHAT_ID.strip():
        logging.warning(f"TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is empty, skipping message: {message[:50]}...")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    
    for attempt in range(3):
        try:
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            logging.info(f"Telegram sent successfully: {message[:50]}...")
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Telegram attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    
    logging.error("Failed to send Telegram message after 3 attempts")
    return False

def fetch_enhanced_market_data():
    """Fetch market data with additional historical data for better analysis"""
    logging.info("Fetching enhanced market data from CoinGecko...")
    
    # Get current market data
    url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={TOP_COINS_LIMIT}&page=1&sparkline=true&price_change_percentage=1h,24h,7d"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    
    # Filter data
    data = [coin for coin in response.json() 
            if coin['total_volume'] > MIN_VOLUME 
            and coin['current_price'] > MIN_PRICE
            and not re.search(r'(\d+[LS])$', coin['symbol'].upper())]
    
    logging.info(f"Filtered market data: {len(data)} coins")
    
    # Get smaller tokens (excluding top coins)
    smaller_tokens = [coin for coin in data if data.index(coin) >= TOP_COINS_TO_EXCLUDE]
    
    # Always include main tokens
    for token_id in MAIN_TOKENS:
        if not any(coin['id'] == token_id for coin in smaller_tokens):
            main_coin = next((coin for coin in data if coin['id'] == token_id), None)
            if main_coin:
                smaller_tokens.append(main_coin)
            else:
                # Direct fetch for missing main tokens
                try:
                    direct_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={token_id}&sparkline=true&price_change_percentage=1h,24h,7d"
                    direct_response = requests.get(direct_url, timeout=10)
                    direct_response.raise_for_status()
                    direct_data = direct_response.json()
                    if direct_data and direct_data[0]['total_volume'] > MIN_VOLUME:
                        smaller_tokens.append(direct_data[0])
                except Exception as e:
                    logging.error(f"Failed to fetch {token_id}: {e}")
    
    logging.info(f"Final enhanced market data: {len(smaller_tokens)} coins")
    return smaller_tokens

def analyze_coin_for_grid(coin: Dict) -> Optional[Dict]:
    """Comprehensive analysis of a coin for grid trading suitability"""
    try:
        symbol = coin['symbol'].upper()
        current_price = coin['current_price']
        sparkline = coin['sparkline_in_7d']['price']
        market_cap = coin['market_cap']
        volume_24h = coin['total_volume']
        
        if not sparkline or len(sparkline) < 50:
            logging.warning(f"Insufficient price data for {symbol}")
            return None
        
        # Market regime analysis
        regime = MarketRegime.analyze_trend(sparkline)
        if not regime['suitable_for_grid']:
            logging.info(f"Skipping {symbol}: unsuitable trend ({regime['trend']})")
            return None
        
        # Support/Resistance analysis
        sr_levels = MarketRegime.detect_support_resistance(sparkline)
        
        # Enhanced technical indicators
        rsi_data = EnhancedIndicators.rsi_enhanced(sparkline)
        bb_data = EnhancedIndicators.bollinger_bands(sparkline)
        
        if not rsi_data or not bb_data:
            logging.warning(f"Insufficient indicator data for {symbol}")
            return None
        
        # Volume analysis (use mock volume data based on 24h volume)
        mock_volumes = [volume_24h * (0.8 + 0.4 * abs(math.sin(i * 0.1))) for i in range(len(sparkline))]
        volume_data = EnhancedIndicators.volume_analysis(mock_volumes, sparkline)
        
        # Market cap classification
        if market_cap >= 50_000_000_000:
            market_tier = "mega"
            base_params = {"spacing": 0.003, "buffer": 0.06, "max_grids": 200}
        elif market_cap >= 10_000_000_000:
            market_tier = "large"
            base_params = {"spacing": 0.004, "buffer": 0.08, "max_grids": 150}
        elif market_cap >= 1_000_000_000:
            market_tier = "mid"
            base_params = {"spacing": 0.006, "buffer": 0.10, "max_grids": 120}
        else:
            market_tier = "small"
            base_params = {"spacing": 0.008, "buffer": 0.12, "max_grids": 100}
        
        # Calculate volatility
        returns = [(sparkline[i] / sparkline[i-1] - 1) for i in range(1, len(sparkline))]
        volatility = statistics.stdev(returns) if len(returns) > 1 else 0.05
        
        # Adjust parameters based on analysis
        volatility_multiplier = min(2.0, max(0.5, volatility / 0.1))
        adjusted_spacing = base_params["spacing"] * volatility_multiplier
        
        # Determine grid range using multiple factors
        bb_range_factor = 1.2 if bb_data["squeeze"] else 1.0
        
        # Use Bollinger Bands and Support/Resistance for range
        suggested_min = min(bb_data["lower"], sr_levels["support"] * 0.98)
        suggested_max = max(bb_data["upper"], sr_levels["resistance"] * 1.02)
        
        # Apply safety buffers
        buffer = base_params["buffer"] * bb_range_factor
        min_price = suggested_min * (1 - buffer)
        max_price = suggested_max * (1 + buffer)
        
        # Ensure minimum range
        range_pct = (max_price - min_price) / current_price
        if range_pct < MIN_RANGE_EFFICIENCY:
            adjustment = (MIN_RANGE_EFFICIENCY * current_price - (max_price - min_price)) / 2
            min_price -= adjustment
            max_price += adjustment
        
        # Calculate optimal grid count
        theoretical_grids = (max_price - min_price) / (current_price * adjusted_spacing)
        optimal_grids = max(20, min(base_params["max_grids"], int(theoretical_grids)))
        
        # Risk management
        risk_mgr = RiskManager()
        profit_potential = risk_mgr.estimate_grid_profit_potential(
            min_price, max_price, current_price, optimal_grids, volatility
        )
        
        if profit_potential["potential_daily_profit"] < MIN_PROFIT_POTENTIAL:
            logging.info(f"Skipping {symbol}: insufficient profit potential")
            return None
        
        # Direction and confidence
        direction_score = 0
        confidence_factors = []
        
        # RSI signals
        if rsi_data["oversold"]:
            direction_score += 2
            confidence_factors.append("RSI oversold")
        elif rsi_data["overbought"]:
            direction_score -= 2
            confidence_factors.append("RSI overbought")
        
        # Bollinger Band position
        if bb_data["position"] < 0.2:
            direction_score += 1
            confidence_factors.append("Near BB lower")
        elif bb_data["position"] > 0.8:
            direction_score -= 1
            confidence_factors.append("Near BB upper")
        
        # Support/Resistance proximity
        if sr_levels["support_distance"] < 0.05:
            direction_score += 1
            confidence_factors.append("Near support")
        elif sr_levels["resistance_distance"] < 0.05:
            direction_score -= 1
            confidence_factors.append("Near resistance")
        
        # Determine final direction
        if direction_score >= 2:
            direction = "Long"
            confidence = "High" if len(confidence_factors) >= 2 else "Medium"
        elif direction_score <= -2:
            direction = "Short"
            confidence = "High" if len(confidence_factors) >= 2 else "Medium"
        else:
            direction = "Neutral"
            confidence = "High"  # Neutral is always high confidence for grid trading
        
        return {
            'coin': coin,
            'symbol': symbol,
            'current_price': current_price,
            'min_price': min_price,
            'max_price': max_price,
            'grids': optimal_grids,
            'direction': direction,
            'confidence': confidence,
            'market_tier': market_tier,
            'volatility': volatility,
            'rsi': rsi_data["value"],
            'bb_position': bb_data["position"],
            'trend': regime["trend"],
            'profit_potential': profit_potential,
            'confidence_factors': confidence_factors,
            'volume_strength': volume_data["current_vs_avg"],
            'sr_levels': sr_levels
        }
        
    except Exception as e:
        logging.error(f"Error analyzing {coin.get('symbol', 'unknown')}: {e}")
        return None

def format_price(value):
    """Enhanced price formatting"""
    if value >= 1000:
        return f"${value:,.0f}"
    elif value >= 100:
        return f"${value:.2f}"
    elif value >= 1:
        return f"${value:.4f}"
    elif value >= 0.01:
        return f"${value:.6f}"
    else:
        return f"${value:.10f}"

def main():
    """Main execution with enhanced analysis and filtering"""
    try:
        logging.info("Starting enhanced grid trading analysis...")
        market_data = fetch_enhanced_market_data()
        
        if not market_data:
            send_telegram("⚠️ *Enhanced Grid Bot Alert*\nNo market data available this hour.")
            return
        
        # Analyze all coins
        analyzed_coins = []
        for coin in market_data:
            analysis = analyze_coin_for_grid(coin)
            if analysis:
                analyzed_coins.append(analysis)
        
        if not analyzed_coins:
            send_telegram("⚠️ *Enhanced Grid Bot Alert*\nNo suitable grid opportunities found this hour.")
            return
        
        # Sort by profit potential and confidence
        analyzed_coins.sort(key=lambda x: (
            x['confidence'] == 'High',
            x['profit_potential']['potential_daily_profit']
        ), reverse=True)
        
        # Filter for correlation (simplified - just avoid too many similar market cap tiers)
        final_recommendations = []
        tier_counts = {}
        
        for analysis in analyzed_coins:
            tier = analysis['market_tier']
            if tier_counts.get(tier, 0) < 2:  # Max 2 per tier
                final_recommendations.append(analysis)
                tier_counts[tier] = tier_counts.get(tier, 0) + 1
            
            if len(final_recommendations) >= 6:  # Max 6 total recommendations
                break
        
        # Generate enhanced message
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        message = f"🤖 *ENHANCED GRID TRADING ANALYSIS — {ts}*\n\n"
        
        main_tokens = [a for a in final_recommendations if a['coin']['id'] in MAIN_TOKENS]
        other_tokens = [a for a in final_recommendations if a['coin']['id'] not in MAIN_TOKENS]
        
        if main_tokens:
            message += "*🏆 MAIN TOKENS*\n"
            for analysis in main_tokens[:2]:
                message += format_enhanced_alert(analysis) + "\n"
        
        if other_tokens:
            message += "*💎 HIGH-POTENTIAL OPPORTUNITIES*\n"
            for analysis in other_tokens[:3]:
                message += format_enhanced_alert(analysis) + "\n"
        
        # Add market summary
        avg_volatility = sum([a['volatility'] for a in final_recommendations]) / len(final_recommendations)
        high_confidence_count = len([a for a in final_recommendations if a['confidence'] == 'High'])
        
        message += f"\n📊 *MARKET SUMMARY*\n"
        message += f"• Average Volatility: `{avg_volatility:.1%}`\n"
        message += f"• High Confidence Setups: `{high_confidence_count}/{len(final_recommendations)}`\n"
        message += f"• Market Regime: Mixed conditions favorable for grid trading\n"
        
        message += f"\n⚠️ *RISK REMINDERS*\n"
        message += f"• Never risk more than 5-10% per grid\n"
        message += f"• Monitor for trend breakouts\n"
        message += f"• Consider correlation between positions\n"
        message += f"• Use stop-losses in volatile conditions"
        
        send_telegram(message)
        logging.info(f"Enhanced analysis complete. Sent {len(final_recommendations)} recommendations.")
        
    except Exception as e:
        logging.error(f"Enhanced analysis error: {e}")
        send_telegram(f"🚨 *Enhanced Grid Bot Error*\n```{str(e)[:200]}```")

def format_enhanced_alert(analysis: Dict) -> str:
    """Format individual coin analysis into alert"""
    symbol = analysis['symbol']
    current_price = analysis['current_price']
    direction = analysis['direction']
    confidence = analysis['confidence']
    
    # Direction emoji
    direction_emoji = {"Long": "🟢", "Short": "🔴", "Neutral": "🟡"}[direction]
    confidence_emoji = "🔥" if confidence == "High" else "⚡"
    
    # Format prices
    price_fmt = format_price(current_price)
    min_fmt = format_price(analysis['min_price'])
    max_fmt = format_price(analysis['max_price'])
    
    alert = f"\n{direction_emoji} *{symbol}* {price_fmt} | RSI {analysis['rsi']:.1f}\n"
    alert += f"📈 *SETUP*: `{min_fmt} - {max_fmt}` ({analysis['grids']} grids)\n"
    alert += f"🎯 *Direction*: `{direction}` {confidence_emoji} | *Tier*: `{analysis['market_tier'].upper()}`\n"
    alert += f"💰 *Daily Profit Est*: `{analysis['profit_potential']['potential_daily_profit']:.1%}`\n"
    
    # Add key factors
    factors = ", ".join(analysis['confidence_factors'][:2])
    if factors:
        alert += f"🔍 *Key Factors*: {factors}\n"
    
    return alert

if __name__ == "__main__":
    main()
