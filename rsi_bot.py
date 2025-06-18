import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path

# ── ENV + CONFIG ─────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API = "https://api.pionex.com/api/v1"
STATE_FILE = Path("active_grids.json")

# Very relaxed thresholds for maximum signal detection
VOL_THRESHOLD = 0.2  # Very low volatility requirement
RSI_OVERSOLD = 45    # Much more relaxed
RSI_OVERBOUGHT = 55  # Much more relaxed
DIAGNOSTIC_MODE = True  # Enable detailed logging

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
        if DIAGNOSTIC_MODE:
            logging.info("Telegram Response: %s", r.json())
        return r.status_code == 200
    except Exception as e:
        logging.error("Telegram error: %s", e)
        print(f"❌ Telegram error: {e}")
        return False

# ── SYMBOL FETCHING WITH DIAGNOSTICS ─────────────────
def fetch_symbols():
    """Retrieve top perpetual trading pairs with diagnostic info."""
    try:
        r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=15)
        if r.status_code != 200:
            print(f"❌ API Error: {r.status_code}")
            return []
        
        tickers = r.json().get("data", {}).get("tickers", [])
        
        # Enhanced filtering with diagnostics
        valid_tickers = []
        for t in tickers:
            if isinstance(t, dict) and 'symbol' in t:
                try:
                    volume = float(t.get('volume', 0))
                    price = float(t.get('close', 0))
                    if volume > 100 and price > 0:  # Very low minimum requirements
                        valid_tickers.append((t["symbol"], volume, price))
                except (ValueError, TypeError):
                    continue
        
        # Sort by volume descending
        valid_tickers.sort(key=lambda x: x[1], reverse=True)
        symbols = [t[0] for t in valid_tickers]
        
        if DIAGNOSTIC_MODE:
            print(f"📊 Symbol Stats:")
            print(f"   Total tickers received: {len(tickers)}")
            print(f"   Valid symbols: {len(symbols)}")
            print(f"   Top 5 by volume: {[s for s in symbols[:5]]}")
        
        print(f"✅ Fetched {len(symbols)} valid symbols")
        return symbols[:50]  # Focus on top 50 for speed
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ── ENHANCED PRICE DATA FETCHING ─────────────────────
def fetch_closes(sym, interval="5M", limit=200):
    """Fetch historical closing prices with diagnostics."""
    try:
        r = requests.get(f"{API}/market/klines", params={
            "symbol": sym, 
            "interval": interval, 
            "limit": limit, 
            "type": "PERP"
        }, timeout=15)
        
        if r.status_code != 200:
            if DIAGNOSTIC_MODE:
                print(f"❌ API Error for {sym}: {r.status_code}")
            return []
            
        payload = r.json().get("data", {}).get("klines", [])
        closes = []
        
        for k in payload:
            if isinstance(k, (list, tuple)) and len(k) > 4:
                try:
                    close_price = float(k[4])
                    if close_price > 0:
                        closes.append(close_price)
                except (ValueError, TypeError):
                    continue
        
        if DIAGNOSTIC_MODE and len(closes) < 50:
            print(f"⚠️  {sym}: Only {len(closes)} price points available")
                    
        return closes
    except Exception as e:
        if DIAGNOSTIC_MODE:
            print(f"❌ Error fetching {sym}: {e}")
        return []

# ── SIMPLIFIED RSI CALCULATION ──────────────────────
def fetch_rsi(sym, interval="5M", period=14):
    """Simplified RSI calculation with diagnostics."""
    closes = fetch_closes(sym, interval, limit=period * 4)
    if len(closes) < period + 5:
        if DIAGNOSTIC_MODE:
            print(f"⚠️  {sym}: Insufficient data for RSI ({len(closes)} points)")
        return None

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, abs(deltas), 0)

    # Simple average instead of EMA for stability
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)

# ── SIMPLIFIED ANALYSIS WITH DIAGNOSTICS ────────────
def analyse(sym, interval="5M", limit=200):
    """Simplified analysis with extensive diagnostics."""
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 30:
        if DIAGNOSTIC_MODE:
            print(f"❌ {sym}: Insufficient price data ({len(closes)} points)")
        return None

    rsi = fetch_rsi(sym, interval)
    if rsi is None:
        if DIAGNOSTIC_MODE:
            print(f"❌ {sym}: RSI calculation failed")
        return None

    # Very simple range calculation
    px = closes[-1]
    low = min(closes[-50:]) if len(closes) >= 50 else min(closes)
    high = max(closes[-50:]) if len(closes) >= 50 else max(closes)
    
    rng = high - low
    if rng <= 0 or px == 0:
        if DIAGNOSTIC_MODE:
            print(f"❌ {sym}: Invalid price range (rng={rng}, px={px})")
        return None

    volatility = round(rng / px * 100, 2)
    
    # More flexible zone detection
    zone = None
    confidence = "Low"
    
    if rsi <= 35:
        zone = "Long"
        confidence = "High"
    elif rsi <= RSI_OVERSOLD:
        zone = "Long"
        confidence = "Medium"
    elif rsi >= 65:
        zone = "Short"
        confidence = "High"
    elif rsi >= RSI_OVERBOUGHT:
        zone = "Short"
        confidence = "Medium"
    elif volatility > 2.0:  # High volatility signals
        if rsi < 50:
            zone = "Long"
            confidence = "Low"
        else:
            zone = "Short"
            confidence = "Low"
    
    result = {
        "symbol": sym,
        "zone": zone,
        "confidence": confidence,
        "low": low,
        "high": high,
        "now": px,
        "rsi": rsi,
        "vol": volatility,
        "timeframe": interval,
        "data_points": len(closes)
    }
    
    if DIAGNOSTIC_MODE:
        status = "✅ SIGNAL" if zone else "❌ NO SIGNAL"
        print(f"{status} {sym}: RSI={rsi:5.1f}, Vol={volatility:5.1f}%, Zone={zone or 'None'}, Price={px:.6f}")
    
    return result if zone else None

# ── DIAGNOSTIC SCAN FUNCTION ────────────────────────
def diagnostic_scan(symbols, max_symbols=20):
    """Run diagnostic scan on a subset of symbols."""
    print(f"\n🔍 DIAGNOSTIC MODE: Analyzing top {max_symbols} symbols in detail...")
    
    results = []
    rsi_distribution = []
    vol_distribution = []
    
    for i, sym in enumerate(symbols[:max_symbols]):
        try:
            print(f"\n--- Analyzing {sym} ({i+1}/{max_symbols}) ---")
            res = analyse(sym, interval="5M", limit=200)
            
            if res:
                results.append(res)
                rsi_distribution.append(res["rsi"])
                vol_distribution.append(res["vol"])
            else:
                # Still collect stats even for non-signals
                rsi = fetch_rsi(sym, "5M")
                if rsi:
                    rsi_distribution.append(rsi)
            
        except Exception as e:
            print(f"❌ Error in diagnostic for {sym}: {e}")
    
    # Print diagnostic summary
    print(f"\n📊 DIAGNOSTIC SUMMARY:")
    print(f"   Symbols analyzed: {max_symbols}")
    print(f"   Signals found: {len(results)}")
    
    if rsi_distribution:
        print(f"   RSI Range: {min(rsi_distribution):.1f} - {max(rsi_distribution):.1f}")
        print(f"   RSI Average: {sum(rsi_distribution)/len(rsi_distribution):.1f}")
        print(f"   RSI <= {RSI_OVERSOLD}: {sum(1 for r in rsi_distribution if r <= RSI_OVERSOLD)}")
        print(f"   RSI >= {RSI_OVERBOUGHT}: {sum(1 for r in rsi_distribution if r >= RSI_OVERBOUGHT)}")
    
    if vol_distribution:
        print(f"   Vol Range: {min(vol_distribution):.1f}% - {max(vol_distribution):.1f}%")
        print(f"   Vol Average: {sum(vol_distribution)/len(vol_distribution):.1f}%")
        print(f"   Vol >= {VOL_THRESHOLD}%: {sum(1 for v in vol_distribution if v >= VOL_THRESHOLD)}")
    
    return results

# ── STATE MANAGEMENT ────────────────────────────────
def load_state():
    """Load bot state."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_state(d):
    """Save bot state."""
    with open(STATE_FILE, 'w') as f:
        json.dump(d, f, indent=2)

# ── NOTIFICATION SYSTEM ─────────────────────────────
def notify_trade(sym, data):
    """Send trade notifications."""
    confidence_emoji = {"High": "🔥", "Medium": "⚡", "Low": "💡"}[data['confidence']]
    zone_emoji = "🟢" if data['zone'] == "Long" else "🔴"
    
    msg = (f"{confidence_emoji} *{data['confidence']}* Signal: *{sym}*\n"
           f"{zone_emoji} Zone: *{data['zone']}*\n"
           f"📊 RSI: *{data['rsi']}* | Vol: *{data['vol']}%*\n"
           f"💰 Price: *{data['now']:.6f}*\n"
           f"📏 Range: {data['low']:.6f} – {data['high']:.6f}")
    
    return tg(msg)

# ── MAIN FUNCTION WITH DIAGNOSTICS ──────────────────
def main():
    """Execute bot with diagnostic capabilities."""
    print("🤖 Starting RSI Bot with Diagnostics...")
    
    if DIAGNOSTIC_MODE:
        tg("🔍 RSI Bot started in DIAGNOSTIC MODE")
    else:
        tg("🚀 RSI Bot started - Scanning for opportunities...")
    
    start_time = time.time()
    prev = load_state()
    nxt, trades = {}, []
    
    symbols = fetch_symbols()
    if not symbols:
        tg("❌ Could not fetch symbols from API")
        return
    
    # Run diagnostic scan first
    if DIAGNOSTIC_MODE:
        diagnostic_results = diagnostic_scan(symbols, max_symbols=15)
        trades.extend(diagnostic_results)
    
    # Regular scan on remaining symbols
    analyzed = 0
    for sym in symbols[15 if DIAGNOSTIC_MODE else 0:]:
        try:
            res = analyse(sym, interval="5M", limit=200)
            analyzed += 1
            
            if res:
                nxt[sym] = {
                    "zone": res["zone"],
                    "confidence": res["confidence"],
                    "rsi": res["rsi"],
                    "vol": res["vol"]
                }
                
                # Check for new signals
                if sym not in prev or prev[sym].get("zone") != res["zone"]:
                    trades.append(res)
                    
        except Exception as e:
            if DIAGNOSTIC_MODE:
                print(f"❌ Error analyzing {sym}: {e}")

    save_state(nxt)
    
    execution_time = round(time.time() - start_time, 1)
    
    # Send results
    summary_msg = (f"🔍 *Diagnostic Scan Complete*\n"
                  f"📊 Analyzed: {analyzed + (15 if DIAGNOSTIC_MODE else 0)} symbols\n"
                  f"🎯 Signals Found: {len(trades)}\n"
                  f"⏱️ Time: {execution_time}s\n"
                  f"📋 Threshold: RSI {RSI_OVERSOLD}/{RSI_OVERBOUGHT}, Vol {VOL_THRESHOLD}%")
    
    tg(summary_msg)
    
    if trades:
        print(f"🚨 Found {len(trades)} signals!")
        # Sort by confidence and send notifications
        trades.sort(key=lambda x: {"High": 3, "Medium": 2, "Low": 1}[x["confidence"]], reverse=True)
        
        for trade in trades:
            notify_trade(trade["symbol"], trade)
            time.sleep(1)
    else:
        print("😴 No signals found even with relaxed criteria")
        
        # Send diagnostic message with suggestions
        diag_msg = ("🔧 *No signals detected*\n"
                   "Current market may be:\n"
                   "• Low volatility consolidation\n"
                   "• RSI in neutral zone (45-55)\n"
                   "• Consider manual review of top symbols")
        tg(diag_msg)

if __name__ == "__main__":
    main()
