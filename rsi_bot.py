import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path
import sys

# ── ENV + CONFIG ─────────────────────────────────────

TG_TOKEN = os.environ.get(“TG_TOKEN”, os.environ.get(“TELEGRAM_TOKEN”, “”)).strip()
TG_CHAT_ID = os.environ.get(“TG_CHAT_ID”, os.environ.get(“TELEGRAM_CHAT_ID”, “”)).strip()

API = “https://api.pionex.com/api/v1”
STATE_FILE = Path(“active_grids.json”)

# EXTREMELY relaxed thresholds for debugging

VOL_THRESHOLD = 0.001  # 0.001% - almost any movement
RSI_OVERSOLD = 90      # Almost impossible not to trigger
RSI_OVERBOUGHT = 10    # Almost impossible not to trigger
DEBUG_MODE = True

print(f”🐛 ENHANCED DEBUG MODE: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT} (Short), RSI≥{RSI_OVERSOLD} (Long)”)
sys.stdout.flush()

# ── TELEGRAM NOTIFICATION ───────────────────────────

def tg(msg):
if not TG_TOKEN or not TG_CHAT_ID:
print(“❌ Missing Telegram credentials”)
return False
try:
r = requests.post(
f”https://api.telegram.org/bot{TG_TOKEN}/sendMessage”,
json={“chat_id”: TG_CHAT_ID, “text”: msg, “parse_mode”: “Markdown”},
timeout=10
)
return r.status_code == 200
except Exception as e:
print(f”❌ Telegram error: {e}”)
return False

# ── SYMBOL FETCHING ──────────────────────────────────

def fetch_symbols():
“”“Get symbols with debug info.”””
try:
print(“🔍 Fetching symbols from API…”)
sys.stdout.flush()

```
    r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=15)
    print(f"📡 API Response status: {r.status_code}")
    sys.stdout.flush()
    
    if r.status_code != 200:
        print(f"❌ API Error: {r.status_code}")
        print(f"❌ Response text: {r.text[:500]}")
        sys.stdout.flush()
        return []
    
    data = r.json()
    print(f"🔍 API Response keys: {list(data.keys())}")
    sys.stdout.flush()
    
    tickers = data.get("data", {}).get("tickers", [])
    print(f"📊 Raw tickers count: {len(tickers)}")
    sys.stdout.flush()
    
    if len(tickers) > 0:
        print(f"📋 First ticker sample: {tickers[0]}")
        sys.stdout.flush()
    
    symbols = []
    for i, t in enumerate(tickers[:10]):  # Check first 10 in detail
        if isinstance(t, dict) and 'symbol' in t:
            symbols.append(t["symbol"])
            if DEBUG_MODE:
                print(f"   {i+1:2d}: {t['symbol']:<15} - Close: {t.get('close', 'N/A')}")
                sys.stdout.flush()
    
    print(f"✅ Extracted {len(symbols)} symbols from first 10")
    sys.stdout.flush()
    
    # Get more symbols
    for t in tickers[10:]:
        if isinstance(t, dict) and 'symbol' in t:
            symbols.append(t["symbol"])
    
    print(f"✅ Total symbols extracted: {len(symbols)}")
    limited_symbols = symbols[:5]  # Focus on just 5 for detailed debug
    print(f"🎯 Will analyze these {len(limited_symbols)} symbols: {limited_symbols}")
    sys.stdout.flush()
    
    return limited_symbols
    
except Exception as e:
    print(f"❌ Exception fetching symbols: {e}")
    import traceback
    traceback.print_exc()
    sys.stdout.flush()
    return []
```

# ── PRICE DATA FETCHING ──────────────────────────────

def fetch_closes(sym, interval=“5M”, limit=100):
“”“Fetch closes with enhanced debug.”””
try:
print(f”  📥 Fetching {sym} klines data…”)
sys.stdout.flush()

```
    url = f"{API}/market/klines"
    params = {
        "symbol": sym, 
        "interval": interval, 
        "limit": limit, 
        "type": "PERP"
    }
    print(f"  📡 Request URL: {url}")
    print(f"  📡 Request params: {params}")
    sys.stdout.flush()
    
    r = requests.get(url, params=params, timeout=15)
    print(f"  📡 Response status: {r.status_code}")
    sys.stdout.flush()
    
    if r.status_code != 200:
        print(f"    ❌ HTTP {r.status_code} for {sym}")
        print(f"    ❌ Response: {r.text[:200]}")
        sys.stdout.flush()
        return []
    
    data = r.json()
    print(f"  📊 Response keys: {list(data.keys())}")
    sys.stdout.flush()
    
    klines = data.get("data", {}).get("klines", [])
    print(f"  📊 {sym}: Got {len(klines)} klines")
    sys.stdout.flush()
    
    if not klines:
        print(f"    ⚠️  No klines data for {sym}")
        sys.stdout.flush()
        return []
    
    # Debug first few klines structure
    print(f"  📋 First 3 klines structure:")
    for i, k in enumerate(klines[:3]):
        print(f"    {i+1}: {k} (type: {type(k)}, len: {len(k) if hasattr(k, '__len__') else 'N/A'})")
        sys.stdout.flush()
    
    closes = []
    for i, k in enumerate(klines):
        try:
            # Handle both dictionary format (current API) and array format
            if isinstance(k, dict):
                # Dictionary format: {'close': '0.04586', ...}
                close_str = k.get('close')
                if close_str:
                    close = float(close_str)
                    if close > 0:
                        closes.append(close)
                        if i < 3:  # Debug first 3
                            print(f"    Kline {i+1}: Close = {close} (from dict)")
                            sys.stdout.flush()
            elif isinstance(k, (list, tuple)) and len(k) > 4:
                # Array format: [timestamp, open, high, low, close, volume]
                close = float(k[4])
                if close > 0:
                    closes.append(close)
                    if i < 3:  # Debug first 3
                        print(f"    Kline {i+1}: Close = {close} (from array)")
                        sys.stdout.flush()
            else:
                if i < 3:  # Only log first few to avoid spam
                    print(f"    ❌ Unknown kline format {i+1}: {type(k)} - {k}")
                    sys.stdout.flush()
                    
        except (ValueError, TypeError) as e:
            if i < 3:  # Only log first few errors
                print(f"    ❌ Error parsing kline {i+1}: {e}")
                sys.stdout.flush()
    
    print(f"  ✅ {sym}: Extracted {len(closes)} valid closes")
    if len(closes) > 0:
        print(f"    💰 Price range: {min(closes):.8f} - {max(closes):.8f}")
        print(f"    📈 Latest price: {closes[-1]:.8f}")
        print(f"    📊 Sample closes: {closes[:5]} ... {closes[-3:]}")
    sys.stdout.flush()
    
    return closes
    
except Exception as e:
    print(f"  ❌ Exception fetching {sym}: {e}")
    import traceback
    traceback.print_exc()
    sys.stdout.flush()
    return []
```

# ── RSI CALCULATION ──────────────────────────────────

def calc_rsi(sym, closes, period=14):
“”“Calculate RSI with enhanced debug.”””
print(f”  📈 Calculating RSI for {sym}…”)
print(f”    📊 Closes count: {len(closes)}, Period: {period}”)
sys.stdout.flush()

```
if len(closes) < period + 5:
    print(f"    ❌ {sym}: Need {period+5}+ closes, got {len(closes)}")
    sys.stdout.flush()
    return None

deltas = np.diff(closes)
print(f"    📊 Deltas count: {len(deltas)}")
print(f"    📊 Sample deltas: {deltas[:5]}")
sys.stdout.flush()

gains = np.where(deltas > 0, deltas, 0)
losses = np.where(deltas < 0, abs(deltas), 0)

print(f"    📊 Gains: count={len(gains)}, avg={np.mean(gains):.8f}, max={np.max(gains):.8f}")
print(f"    📊 Losses: count={len(losses)}, avg={np.mean(losses):.8f}, max={np.max(losses):.8f}")
sys.stdout.flush()

avg_gain = np.mean(gains[-period:])
avg_loss = np.mean(losses[-period:])

print(f"    📊 Avg gain (last {period}): {avg_gain:.8f}")
print(f"    📊 Avg loss (last {period}): {avg_loss:.8f}")
sys.stdout.flush()

if avg_loss == 0:
    rsi = 100
    print(f"    📈 RSI = 100 (no losses)")
else:
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    print(f"    📊 RS ratio: {rs:.6f}")
    print(f"    📈 RSI calculation: 100 - (100 / (1 + {rs:.6f})) = {rsi:.2f}")

sys.stdout.flush()
return round(rsi, 2)
```

# ── MAIN ANALYSIS ────────────────────────────────────

def analyze_symbol(sym):
“”“Analyze single symbol with ENHANCED debug.”””
print(f”\n🔍 ANALYZING {sym}”)
print(”=” * 50)
sys.stdout.flush()

```
# Get price data with EXTRA debug
print(f"  📥 Step 1: Fetching price data for {sym}...")
sys.stdout.flush()

closes = fetch_closes(sym, "5M", 100)
print(f"  📊 Step 1 Result: Got {len(closes)} closes")
sys.stdout.flush()

if len(closes) < 20:
    print(f"❌ {sym}: INSUFFICIENT DATA - need 20+, got {len(closes)} - SKIPPING")
    sys.stdout.flush()
    return None

# Calculate RSI with EXTRA debug
print(f"  📈 Step 2: Calculating RSI for {sym}...")
sys.stdout.flush()

rsi = calc_rsi(sym, closes)
print(f"  📈 Step 2 Result: RSI = {rsi}")
sys.stdout.flush()

if rsi is None:
    print(f"❌ {sym}: RSI CALCULATION FAILED - SKIPPING")
    sys.stdout.flush()
    return None

# Calculate volatility with EXTRA debug
print(f"  📊 Step 3: Calculating volatility for {sym}...")
sys.stdout.flush()

recent_closes = closes[-50:] if len(closes) >= 50 else closes
low = min(recent_closes)
high = max(recent_closes)
current = closes[-1]

if high == low:
    volatility = 0
else:
    volatility = ((high - low) / current) * 100

print(f"  📊 Step 3 Result: Volatility = {volatility:.6f}%")
print(f"    💰 Current: {current:.8f}")
print(f"    📉 Low: {low:.8f}")
print(f"    📈 High: {high:.8f}")
print(f"    📊 Range: {high - low:.8f}")
sys.stdout.flush()

# ULTRA DETAILED signal logic
print(f"  🎯 Step 4: Signal Detection for {sym}...")
print(f"    🔍 Current thresholds:")
print(f"      VOL_THRESHOLD: {VOL_THRESHOLD}%")
print(f"      RSI_OVERBOUGHT: {RSI_OVERBOUGHT} (Short trigger)")
print(f"      RSI_OVERSOLD: {RSI_OVERSOLD} (Long trigger)")
sys.stdout.flush()

zone = None
reason = "No signal"

print(f"    🔍 Volatility check: {volatility:.6f}% >= {VOL_THRESHOLD}% ?")
vol_pass = volatility >= VOL_THRESHOLD
print(f"    📊 Volatility check result: {vol_pass}")
sys.stdout.flush()

if not vol_pass:
    reason = f"REJECTED: Low volatility ({volatility:.6f}% < {VOL_THRESHOLD}%)"
    print(f"    ❌ {reason}")
    sys.stdout.flush()
else:
    print(f"    ✅ Volatility check PASSED")
    
    print(f"    🔍 RSI checks:")
    print(f"      Short check: {rsi} <= {RSI_OVERBOUGHT} ? {rsi <= RSI_OVERBOUGHT}")
    print(f"      Long check: {rsi} >= {RSI_OVERSOLD} ? {rsi >= RSI_OVERSOLD}")
    sys.stdout.flush()
    
    if rsi <= RSI_OVERBOUGHT:
        zone = "Short"
        reason = f"✅ SHORT SIGNAL: RSI {rsi} <= {RSI_OVERBOUGHT}"
        print(f"    🎯 SHORT SIGNAL DETECTED!")
    elif rsi >= RSI_OVERSOLD:
        zone = "Long" 
        reason = f"✅ LONG SIGNAL: RSI {rsi} >= {RSI_OVERSOLD}"
        print(f"    🎯 LONG SIGNAL DETECTED!")
    else:
        reason = f"REJECTED: RSI {rsi} in neutral zone ({RSI_OVERBOUGHT} < RSI < {RSI_OVERSOLD})"
        print(f"    ❌ {reason}")
    
    sys.stdout.flush()

print(f"  🎯 FINAL DECISION: {reason}")
sys.stdout.flush()

if zone:
    print(f"  ✅ RETURNING SIGNAL DATA")
    result = {
        "symbol": sym,
        "zone": zone,
        "rsi": rsi,
        "vol": volatility,
        "price": current,
        "low": low,
        "high": high
    }
    print(f"  📋 Signal data: {result}")
    sys.stdout.flush()
    return result
else:
    print(f"  ❌ RETURNING NONE (no signal)")
    sys.stdout.flush()
    return None
```

# ── MAIN FUNCTION ────────────────────────────────────

def main():
“”“Enhanced debug run.”””
print(“🐛 RSI BOT - ENHANCED DEBUG MODE”)
print(”=” * 60)
print(f”🎯 Thresholds: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT}(Short)|≥{RSI_OVERSOLD}(Long)”)
sys.stdout.flush()

```
# Send debug start message
start_msg = f"🐛 Enhanced Debug Started\nVol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT}|≥{RSI_OVERSOLD}"
tg(start_msg)

# Get symbols
print("\n📡 FETCHING SYMBOLS...")
sys.stdout.flush()
symbols = fetch_symbols()

if not symbols:
    print("❌ NO SYMBOLS TO ANALYZE - EXITING")
    sys.stdout.flush()
    return

print(f"\n🎯 SYMBOLS TO ANALYZE: {symbols}")
sys.stdout.flush()

# Analyze each symbol
signals = []
analyzed_count = 0

for i, sym in enumerate(symbols, 1):
    try:
        print(f"\n🔄 PROCESSING {i}/{len(symbols)}: {sym}")
        print("-" * 60)
        sys.stdout.flush()
        
        result = analyze_symbol(sym)
        analyzed_count += 1
        
        if result:
            signals.append(result)
            print(f"🚨 SIGNAL #{len(signals)} FOUND: {sym} - {result['zone']}")
            sys.stdout.flush()
        else:
            print(f"😴 No signal for {sym}")
            sys.stdout.flush()
    
    except Exception as e:
        print(f"💥 ERROR analyzing {sym}: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()

# Final summary
print(f"\n📊 ENHANCED DEBUG SUMMARY")
print("=" * 40)
print(f"🔍 Symbols analyzed: {analyzed_count}")
print(f"🎯 Signals found: {len(signals)}")
print(f"📋 Criteria: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT} OR RSI≥{RSI_OVERSOLD}")
sys.stdout.flush()

summary_msg = f"🐛 *Enhanced Debug Complete*\n📊 Analyzed: {analyzed_count}\n🎯 Signals: {len(signals)}"

if signals:
    print(f"\n🚨 SIGNALS DETECTED:")
    for s in signals:
        signal_detail = f"  • {s['symbol']}: {s['zone']} | RSI: {s['rsi']} | Vol: {s['vol']:.4f}% | Price: {s['price']:.8f}"
        print(signal_detail)
        
    summary_msg += f"\n🎯 Signals found!"
    
    # Send individual notifications
    for signal in signals:
        signal_msg = (f"🎯 *{signal['symbol']}* - {signal['zone']}\n"
                     f"📊 RSI: {signal['rsi']}\n"
                     f"📈 Vol: {signal['vol']:.4f}%\n"
                     f"💰 Price: {signal['price']:.8f}")
        tg(signal_msg)
        time.sleep(1)
else:
    print(f"\n😴 NO SIGNALS FOUND")
    print(f"🤔 With thresholds Vol≥{VOL_THRESHOLD}% and RSI≤{RSI_OVERBOUGHT}|≥{RSI_OVERSOLD}")
    print(f"🤔 This suggests either:")
    print(f"   • All volatility < {VOL_THRESHOLD}%")
    print(f"   • All RSI between {RSI_OVERBOUGHT}-{RSI_OVERSOLD}")
    print(f"   • API/data issues")
    
    summary_msg += "\n😴 No signals with relaxed thresholds"

sys.stdout.flush()
tg(summary_msg)
```

if **name** == “**main**”:
main()
