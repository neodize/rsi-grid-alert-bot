import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path

# ── ENV + CONFIG ─────────────────────────────────────

TG_TOKEN = os.environ.get(“TG_TOKEN”, os.environ.get(“TELEGRAM_TOKEN”, “”)).strip()
TG_CHAT_ID = os.environ.get(“TG_CHAT_ID”, os.environ.get(“TELEGRAM_CHAT_ID”, “”)).strip()

API = “https://api.pionex.com/api/v1”
STATE_FILE = Path(“active_grids.json”)

# EXTREMELY relaxed thresholds for debugging

VOL_THRESHOLD = 0.01  # 0.01% - almost any movement
RSI_OVERSOLD = 60     # Very high to catch more longs  
RSI_OVERBOUGHT = 40   # Very low to catch more shorts
DEBUG_MODE = True

print(f”🐛 DEBUG MODE: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERSOLD} (Long), RSI≥{RSI_OVERBOUGHT} (Short)”)

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
r = requests.get(f”{API}/market/tickers”, params={“type”: “PERP”}, timeout=15)
if r.status_code != 200:
print(f”❌ API Error: {r.status_code}”)
return []

```
    data = r.json()
    print(f"🔍 API Response structure: {list(data.keys())}")
    
    tickers = data.get("data", {}).get("tickers", [])
    print(f"📊 Raw tickers count: {len(tickers)}")
    
    if len(tickers) > 0:
        print(f"📋 First ticker sample: {tickers[0]}")
    
    symbols = []
    for i, t in enumerate(tickers[:10]):  # Check first 10 in detail
        if isinstance(t, dict) and 'symbol' in t:
            symbols.append(t["symbol"])
            if DEBUG_MODE:
                print(f"   {i+1:2d}: {t['symbol']:<15} - {t.get('close', 'N/A')}")
    
    print(f"✅ Extracted {len(symbols)} symbols from first 10")
    
    # Get more symbols
    for t in tickers[10:]:
        if isinstance(t, dict) and 'symbol' in t:
            symbols.append(t["symbol"])
    
    print(f"✅ Total symbols: {len(symbols)}")
    return symbols[:20]  # Focus on top 20 for detailed debug
    
except Exception as e:
    print(f"❌ Error fetching symbols: {e}")
    return []
```

# ── PRICE DATA FETCHING ──────────────────────────────

def fetch_closes(sym, interval=“5M”, limit=100):
“”“Fetch closes with debug.”””
try:
print(f”  📥 Fetching {sym} data…”)
r = requests.get(f”{API}/market/klines”, params={
“symbol”: sym,
“interval”: interval,
“limit”: limit,
“type”: “PERP”
}, timeout=15)

```
    if r.status_code != 200:
        print(f"    ❌ HTTP {r.status_code} for {sym}")
        return []
    
    data = r.json()
    klines = data.get("data", {}).get("klines", [])
    print(f"  📊 {sym}: Got {len(klines)} klines")
    
    if not klines:
        print(f"    ⚠️  No klines data for {sym}")
        return []
    
    # Debug first kline structure
    if len(klines) > 0:
        print(f"  📋 Sample kline: {klines[0]}")
    
    closes = []
    for k in klines:
        if isinstance(k, (list, tuple)) and len(k) > 4:
            try:
                close = float(k[4])
                if close > 0:
                    closes.append(close)
            except (ValueError, TypeError) as e:
                print(f"    ❌ Error parsing close price: {e}")
    
    print(f"  ✅ {sym}: Extracted {len(closes)} valid closes")
    if len(closes) > 0:
        print(f"    💰 Price range: {min(closes):.6f} - {max(closes):.6f}")
        print(f"    📈 Latest: {closes[-1]:.6f}")
    
    return closes
    
except Exception as e:
    print(f"  ❌ Exception fetching {sym}: {e}")
    return []
```

# ── RSI CALCULATION ──────────────────────────────────

def calc_rsi(sym, closes, period=14):
“”“Calculate RSI with debug.”””
if len(closes) < period + 5:
print(f”  ❌ {sym}: Need {period+5}+ closes, got {len(closes)}”)
return None

```
deltas = np.diff(closes)
gains = np.where(deltas > 0, deltas, 0)
losses = np.where(deltas < 0, abs(deltas), 0)

print(f"  📊 {sym}: Gains avg={np.mean(gains):.8f}, Losses avg={np.mean(losses):.8f}")

avg_gain = np.mean(gains[-period:])
avg_loss = np.mean(losses[-period:])

if avg_loss == 0:
    rsi = 100
else:
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

print(f"  📈 {sym}: RSI = {rsi:.2f}")
return round(rsi, 2)
```

# ── MAIN ANALYSIS ────────────────────────────────────

def analyze_symbol(sym):
“”“Analyze single symbol with full debug.”””
print(f”\n🔍 ANALYZING {sym}”)
print(”=” * 50)

```
# Get price data
closes = fetch_closes(sym, "5M", 100)
if len(closes) < 20:
    print(f"❌ {sym}: Insufficient data ({len(closes)} closes)")
    return None

# Calculate RSI
rsi = calc_rsi(sym, closes)
if rsi is None:
    print(f"❌ {sym}: RSI calculation failed")
    return None

# Calculate volatility
recent_closes = closes[-50:] if len(closes) >= 50 else closes
low = min(recent_closes)
high = max(recent_closes)
current = closes[-1]

if high == low:
    volatility = 0
else:
    volatility = ((high - low) / current) * 100

print(f"  📊 {sym} METRICS:")
print(f"    💰 Current Price: {current:.6f}")
print(f"    📈 RSI: {rsi}")
print(f"    📊 Volatility: {volatility:.2f}%")
print(f"    📏 Range: {low:.6f} - {high:.6f}")

# Determine signal
zone = None
reason = "No signal"

# Check volatility first
if volatility < VOL_THRESHOLD:
    reason = f"Low volatility ({volatility:.2f}% < {VOL_THRESHOLD}%)"
# Check RSI conditions
elif rsi <= RSI_OVERBOUGHT:  # Remember we swapped these for debugging
    zone = "Short"
    reason = f"RSI {rsi} <= {RSI_OVERBOUGHT} (Short signal)"
elif rsi >= RSI_OVERSOLD:
    zone = "Long" 
    reason = f"RSI {rsi} >= {RSI_OVERSOLD} (Long signal)"
else:
    reason = f"RSI {rsi} in neutral zone ({RSI_OVERBOUGHT}-{RSI_OVERSOLD})"

print(f"  🎯 DECISION: {reason}")

if zone:
    print(f"  ✅ SIGNAL DETECTED: {zone}")
    return {
        "symbol": sym,
        "zone": zone,
        "rsi": rsi,
        "vol": volatility,
        "price": current,
        "low": low,
        "high": high
    }
else:
    print(f"  ❌ NO SIGNAL")
    return None
```

# ── MAIN FUNCTION ────────────────────────────────────

def main():
“”“Debug run.”””
print(“🐛 RSI BOT - FULL DEBUG MODE”)
print(”=” * 60)

```
# Send debug start message
tg(f"🐛 Debug scan started\nThresholds: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT}|≥{RSI_OVERSOLD}")

# Get symbols
symbols = fetch_symbols()
if not symbols:
    print("❌ No symbols to analyze")
    return

print(f"\n🎯 Will analyze {len(symbols)} symbols in detail:")
for i, sym in enumerate(symbols, 1):
    print(f"  {i:2d}: {sym}")

# Analyze each symbol
signals = []
analyzed_count = 0

for sym in symbols:
    try:
        result = analyze_symbol(sym)
        analyzed_count += 1
        
        if result:
            signals.append(result)
            print(f"🚨 SIGNAL #{len(signals)}: {sym} - {result['zone']}")
    
    except Exception as e:
        print(f"💥 ERROR analyzing {sym}: {e}")

# Summary
print(f"\n📊 DEBUG SUMMARY")
print("=" * 30)
print(f"🔍 Analyzed: {analyzed_count} symbols")
print(f"🎯 Signals: {len(signals)}")
print(f"📋 Criteria: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT} OR RSI≥{RSI_OVERSOLD}")

summary_msg = f"🐛 *Debug Complete*\n📊 Analyzed: {analyzed_count}\n🎯 Signals: {len(signals)}"

if signals:
    print(f"\n🚨 FOUND {len(signals)} SIGNALS:")
    for s in signals:
        print(f"  • {s['symbol']}: {s['zone']} (RSI: {s['rsi']}, Vol: {s['vol']:.2f}%)")
        
    summary_msg += f"\n✅ Found signals!"
    
    # Send individual signal notifications
    for signal in signals:
        signal_msg = (f"🎯 *{signal['symbol']}* - {signal['zone']}\n"
                     f"📊 RSI: {signal['rsi']}\n"
                     f"📈 Vol: {signal['vol']:.2f}%\n"
                     f"💰 Price: {signal['price']:.6f}")
        tg(signal_msg)
        time.sleep(1)
else:
    print("\n😴 NO SIGNALS FOUND")
    summary_msg += "\n😴 No signals detected"
    
    # This means either:
    # 1. All RSI values are between 40-60
    # 2. All volatility is < 0.01%
    # 3. Data quality issues
    
    print("\n🤔 POSSIBLE REASONS:")
    print("  • All RSI values in 40-60 range (very neutral market)")
    print("  • Volatility below 0.01% (extremely stable)")
    print("  • API data quality issues")
    print("  • Logic error in signal detection")

tg(summary_msg)
```

if **name** == “**main**”:
main()
