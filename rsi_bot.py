import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path

# ── ENV + CONFIG ─────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API = "https://api.pionex.com/api/v1"
STATE_FILE = Path("active_grids.json")

# Enhanced thresholds for better signal detection
VOL_THRESHOLD = 0.5  # Further reduced for more signals
RSI_OVERSOLD = 40    # More conservative
RSI_OVERBOUGHT = 60  # More conservative
MIN_PRICE_CHANGE = 0.02  # 2% minimum price movement

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── TELEGRAM NOTIFICATION ───────────────────────────
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        logging.error("Missing Telegram credentials")
        print("❌ Missing Telegram credentials")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        logging.info("Telegram Response: %s", r.json())
        return r.status_code == 200
    except Exception as e:
        logging.error("Telegram error: %s", e)
        print(f"❌ Telegram error: {e}")
        return False

# ── SYMBOL FETCHING WITH VOLUME FILTERING ────────────
def fetch_symbols():
    """Retrieve top perpetual trading pairs with volume filtering."""
    try:
        r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
        if r.status_code != 200:
            print(f"❌ API Error: {r.status_code}")
            return []
        
        tickers = r.json().get("data", {}).get("tickers", [])
        
        # Filter and sort by volume
        valid_tickers = []
        for t in tickers:
            if isinstance(t, dict) and 'volume' in t and 'symbol' in t:
                try:
                    volume = float(t.get('volume', 0))
                    if volume > 1000:  # Minimum volume filter
                        valid_tickers.append((t["symbol"], volume))
                except (ValueError, TypeError):
                    continue
        
        # Sort by volume descending
        valid_tickers.sort(key=lambda x: x[1], reverse=True)
        symbols = [t[0] for t in valid_tickers]
        
        print(f"✅ Fetched {len(symbols)} symbols with volume > 1000")
        return symbols[:100]  # Increased to top 100
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ── ENHANCED PRICE DATA FETCHING ─────────────────────
def fetch_closes(sym, interval="5M", limit=400):
    """Fetch historical closing prices with error handling."""
    try:
        r = requests.get(f"{API}/market/klines", params={
            "symbol": sym, 
            "interval": interval, 
            "limit": limit, 
            "type": "PERP"
        }, timeout=15)  # Increased timeout
        
        if r.status_code != 200:
            print(f"❌ API Error for {sym}: {r.status_code}")
            return []
            
        payload = r.json().get("data", {}).get("klines", [])
        closes = []
        
        for k in payload:
            if isinstance(k, (list, tuple)) and len(k) > 4:
                try:
                    close_price = float(k[4])
                    if close_price > 0:  # Ensure valid price
                        closes.append(close_price)
                except (ValueError, TypeError):
                    continue
                    
        return closes
    except Exception as e:
        print(f"❌ Error fetching {sym}: {e}")
        return []

# ── IMPROVED RSI CALCULATION ─────────────────────────
def fetch_rsi(sym, interval="5M", period=14):
    """Calculate RSI with improved smoothing."""
    closes = fetch_closes(sym, interval, limit=period * 3)
    if len(closes) < period + 10:  # Need more data for stability
        return None

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, abs(deltas), 0)

    # Use EMA for smoother RSI
    alpha = 1.0 / period
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    for i in range(period, len(gains)):
        avg_gain = alpha * gains[i] + (1 - alpha) * avg_gain
        avg_loss = alpha * losses[i] + (1 - alpha) * avg_loss

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)

# ── BOLLINGER BANDS WITH DYNAMIC PERIOD ─────────────
def fetch_bollinger(sym, interval="5M", period=20):
    """Calculate Bollinger Bands with dynamic adjustment."""
    closes = fetch_closes(sym, interval, limit=period * 2)
    if len(closes) < period:
        return None
    
    mid = np.mean(closes[-period:])
    std_dev = np.std(closes[-period:])
    
    # Dynamic multiplier based on volatility
    multiplier = 2.0 if std_dev / mid > 0.02 else 2.5
    
    upper = mid + (std_dev * multiplier)
    lower = mid - (std_dev * multiplier)
    
    return lower, upper, mid

# ── ENHANCED PRICE ANALYSIS ─────────────────────────
def analyse(sym, interval="5M", limit=400):
    """Enhanced analysis with multiple indicators."""
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 50:
        return None

    rsi = fetch_rsi(sym, interval)
    if rsi is None:
        return None

    # More flexible RSI conditions
    zone = None
    confidence = "Medium"
    
    if rsi <= 30:
        zone = "Long"
        confidence = "High"
    elif rsi <= RSI_OVERSOLD:
        zone = "Long"
        confidence = "Medium"
    elif rsi >= 70:
        zone = "Short"
        confidence = "High"
    elif rsi >= RSI_OVERBOUGHT:
        zone = "Short"
        confidence = "Medium"
    
    if not zone:
        return None

    # Enhanced price range calculation
    boll_result = fetch_bollinger(sym, interval)
    if boll_result:
        boll_lower, boll_upper, boll_mid = boll_result
        # Use recent price action + Bollinger bands
        recent_low = min(closes[-50:])
        recent_high = max(closes[-50:])
        low = max(recent_low, boll_lower * 0.98)  # Slight buffer
        high = min(recent_high, boll_upper * 1.02)
    else:
        low = min(closes[-100:])
        high = max(closes[-100:])

    px = closes[-1]
    rng = high - low
    if rng <= 0 or px == 0:
        return None

    # Calculate price momentum
    price_change = (px - closes[-20]) / closes[-20] * 100 if len(closes) > 20 else 0
    
    # Volatility calculation
    volatility = round(rng / px * 100, 2)
    
    # Only return signals with sufficient volatility
    if volatility < VOL_THRESHOLD:
        return None

    return dict(
        symbol=sym,
        zone=zone,
        confidence=confidence,
        low=low,
        high=high,
        now=px,
        rsi=rsi,
        vol=volatility,
        momentum=round(price_change, 2),
        timeframe=interval
    )

# ── MULTI-TIMEFRAME ANALYSIS ───────────────────────
def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    """Multi-timeframe analysis with fallback."""
    results = []
    
    # Try different timeframes
    for interval, limit in [("60M", 100), ("15M", 150), ("5M", 200)]:
        try:
            result = analyse(sym, interval=interval, limit=limit)
            if result and result["vol"] >= vol_threshold:
                result["timeframe"] = interval
                results.append(result)
        except Exception as e:
            print(f"❌ Error analyzing {sym} on {interval}: {e}")
            continue
    
    # Return best result (highest confidence, then highest volatility)
    if results:
        results.sort(key=lambda x: (
            1 if x["confidence"] == "High" else 0,
            x["vol"]
        ), reverse=True)
        return results[0]
    
    return None

# ── STATE MANAGEMENT WITH TIMESTAMPS ───────────────
def load_state():
    """Load bot state with timestamp validation."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                # Clean old entries (older than 24 hours)
                current_time = time.time()
                clean_data = {}
                for sym, info in data.items():
                    if isinstance(info, dict) and info.get('timestamp', 0) > current_time - 86400:
                        clean_data[sym] = info
                return clean_data
        except json.JSONDecodeError:
            return {}
    return {}

def save_state(d):
    """Save bot state with timestamps."""
    current_time = time.time()
    for sym in d:
        if isinstance(d[sym], dict):
            d[sym]['timestamp'] = current_time
    
    with open(STATE_FILE, 'w') as f:
        json.dump(d, f, indent=2)

# ── ENHANCED NOTIFICATION SYSTEM ───────────────────
def notify_trade(sym, data):
    """Enhanced trade alerts with more details."""
    confidence_emoji = "🔥" if data['confidence'] == "High" else "⚡"
    zone_emoji = "🟢" if data['zone'] == "Long" else "🔴"
    
    msg = (f"{confidence_emoji} *{data['confidence']}* Signal: *{sym}*\n"
           f"{zone_emoji} Zone: *{data['zone']}*\n"
           f"📊 RSI: *{data['rsi']}* | TF: *{data['timeframe']}*\n"
           f"📈 Volatility: *{data['vol']}%*\n"
           f"🎯 Momentum: *{data['momentum']:+.1f}%*\n"
           f"💰 Price: *{data['now']:.6f}*\n"
           f"📏 Range: {data['low']:.6f} – {data['high']:.6f}")
    
    return tg(msg)

# ── MAIN FUNCTION WITH ENHANCED LOGIC ─────────────
def main():
    """Execute enhanced trading bot logic."""
    print("🤖 Starting Enhanced RSI Trading Bot...")
    
    start_time = time.time()
    tg("🚀 Enhanced RSI Bot started - Deep scanning for opportunities...")
    
    prev = load_state()
    nxt, trades = {}, []
    
    symbols = fetch_symbols()
    if not symbols:
        tg("❌ Could not fetch symbols from API")
        return
    
    analyzed = 0
    signals_found = 0
    high_confidence = 0
    
    print(f"🔍 Analyzing {len(symbols)} symbols...")
    
    for i, sym in enumerate(symbols):
        try:
            # Progress indicator
            if i % 20 == 0:
                print(f"📊 Progress: {i}/{len(symbols)} symbols analyzed...")
            
            res = scan_with_fallback(sym)
            analyzed += 1
            
            if not res:
                continue

            nxt[sym] = {
                "zone": res["zone"],
                "confidence": res["confidence"],
                "low": res["low"],
                "high": res["high"],
                "rsi": res["rsi"],
                "vol": res["vol"]
            }

            # Check for new or significantly changed signals
            prev_signal = prev.get(sym, {})
            is_new_signal = (
                sym not in prev or 
                prev_signal.get("zone") != res["zone"] or
                abs(prev_signal.get("rsi", 0) - res["rsi"]) > 10
            )
            
            if is_new_signal:
                trades.append(res)
                signals_found += 1
                if res["confidence"] == "High":
                    high_confidence += 1
                print(f"📈 NEW SIGNAL: {sym} - {res['zone']} (RSI: {res['rsi']}, Conf: {res['confidence']})")
                
        except Exception as e:
            print(f"❌ Error analyzing {sym}: {e}")

    save_state(nxt)
    
    execution_time = round(time.time() - start_time, 1)
    
    # Enhanced summary
    summary_msg = (f"📊 *Enhanced Scan Complete*\n"
                  f"🔍 Analyzed: {analyzed} symbols\n"
                  f"🎯 New Signals: {signals_found}\n"
                  f"🔥 High Confidence: {high_confidence}\n"
                  f"⏱️ Execution: {execution_time}s\n"
                  f"⏰ {time.strftime('%H:%M UTC')}")
    tg(summary_msg)

    # Send notifications for significant signals first
    if trades:
        print(f"🚨 Sending {len(trades)} trade notifications...")
        # Sort by confidence and volatility
        trades.sort(key=lambda x: (
            1 if x["confidence"] == "High" else 0,
            x["vol"]
        ), reverse=True)
        
        for trade in trades:
            notify_trade(trade["symbol"], trade)
            time.sleep(2)  # Rate limiting
    else:
        print("😴 No new signals found")
        tg("😴 No new trading opportunities detected in this scan")

if __name__ == "__main__":
    main()
