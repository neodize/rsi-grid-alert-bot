import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path

# ── ENV + CONFIG ─────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API = "https://api.pionex.com/api/v1"
STATE_FILE = Path("active_grids.json")

# Relaxed thresholds to generate more signals
VOL_THRESHOLD = 1.0  # Reduced from 2.5
RSI_OVERSOLD = 35    # Increased from 30
RSI_OVERBOUGHT = 65  # Decreased from 70

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

# ── SYMBOL FETCHING ───────────────────────────────────
def fetch_symbols():
    """Retrieve the top perpetual trading pairs based on volume."""
    try:
        r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
        if r.status_code != 200:
            print(f"❌ API Error: {r.status_code}")
            return []
        
        tickers = r.json().get("data", {}).get("tickers", [])
        symbols = [t["symbol"] for t in tickers if isinstance(t, dict)]
        print(f"✅ Fetched {len(symbols)} symbols")
        return symbols[:50]  # Limit to top 50 for efficiency
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ── FETCH PRICE DATA ───────────────────────────────────
def fetch_closes(sym, interval="5M", limit=400):
    """Fetch historical closing prices."""
    try:
        r = requests.get(f"{API}/market/klines", params={
            "symbol": sym, 
            "interval": interval, 
            "limit": limit, 
            "type": "PERP"
        }, timeout=10)
        
        if r.status_code != 200:
            return []
            
        payload = r.json().get("data", {}).get("klines", [])
        closes = [float(k[4]) for k in payload if isinstance(k, (list, tuple)) and len(k) > 4]
        return closes
    except Exception as e:
        print(f"❌ Error fetching {sym}: {e}")
        return []

# ── RSI CALCULATION ───────────────────────────────────
def fetch_rsi(sym, interval="5M", period=14):
    """Calculate Relative Strength Index (RSI)."""
    closes = fetch_closes(sym, interval)
    if len(closes) < period + 1:
        return None

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, abs(deltas), 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100  # Extremely bullish

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)

# ── BOLLINGER BANDS VALIDATION ─────────────────────────
def fetch_bollinger(sym, interval="5M"):
    """Calculate Bollinger Bands."""
    closes = fetch_closes(sym, interval)
    if len(closes) < 20:
        return None
    mid = np.mean(closes[-20:])
    std_dev = np.std(closes[-20:])
    upper = mid + (std_dev * 2)
    lower = mid - (std_dev * 2)
    return lower, upper

# ── PRICE ANALYSIS ───────────────────────────────────
def analyse(sym, interval="5M", limit=400):
    """Determine optimal price range with RSI filtering."""
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 30:  # Reduced from 60
        return None

    rsi = fetch_rsi(sym, interval)
    if rsi is None:
        return None

    # More lenient RSI conditions
    zone = None
    if rsi <= RSI_OVERSOLD:
        zone = "Long"
    elif rsi >= RSI_OVERBOUGHT:
        zone = "Short"
    
    if not zone:
        return None

    boll_result = fetch_bollinger(sym, interval)
    if boll_result:
        boll_lower, boll_upper = boll_result
        low = max(min(closes), boll_lower)
        high = min(max(closes), boll_upper)
    else:
        low, high = min(closes), max(closes)

    px = closes[-1]
    rng = high - low
    if rng <= 0 or px == 0:
        return None

    return dict(
        symbol=sym,
        zone=zone,
        low=low,
        high=high,
        now=px,
        rsi=rsi,
        vol=round(rng / px * 100, 1),
    )

# ── TRADING SIGNAL DETECTION ───────────────────────
def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    """Scan symbol with multiple timeframes."""
    # Try 60M first
    r60 = analyse(sym, interval="60M", limit=100)
    if r60 and r60["vol"] >= vol_threshold:
        return r60
    
    # Try 5M if 60M doesn't work or low volatility
    r5 = analyse(sym, interval="5M", limit=200)
    if r5:
        return r5
        
    # Return 60M result even if low volatility
    return r60

# ── STATE MANAGEMENT ───────────────────────────────
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

# ── NOTIFICATION SYSTEM ───────────────────────────
def notify_trade(sym, data):
    """Send Telegram trade alerts."""
    strength = "🔥 STRONG" if (data['rsi'] <= 25 or data['rsi'] >= 75) else "⚡"
    
    msg = (f"{strength} Trade Alert: *{sym}*\n"
           f"🎯 Zone: *{data['zone']}*\n"
           f"📊 RSI: *{data['rsi']}*\n"
           f"📈 Volatility: *{data['vol']}%*\n"
           f"💰 Price: *{data['now']:.6f}*\n"
           f"📏 Range: {data['low']:.6f} – {data['high']:.6f}")
    
    return tg(msg)

# ── MAIN FUNCTION ─────────────────────────────────
def main():
    """Execute trading bot logic."""
    print("🤖 Starting RSI Trading Bot...")
    
    # Send startup message
    tg("🚀 RSI Bot started - Scanning for opportunities...")
    
    prev = load_state()
    nxt, trades = {}, []
    
    symbols = fetch_symbols()
    if not symbols:
        tg("❌ Could not fetch symbols from API")
        return
    
    analyzed = 0
    signals_found = 0
    
    for sym in symbols:
        try:
            res = scan_with_fallback(sym)
            analyzed += 1
            
            if not res:
                continue

            nxt[sym] = {
                "zone": res["zone"],
                "low": res["low"],
                "high": res["high"],
                "rsi": res["rsi"]
            }

            # Check for new or changed signals
            if sym not in prev or prev[sym].get("zone") != res["zone"]:
                trades.append(res)
                signals_found += 1
                print(f"📈 NEW SIGNAL: {sym} - {res['zone']} (RSI: {res['rsi']})")
                
        except Exception as e:
            print(f"❌ Error analyzing {sym}: {e}")

    save_state(nxt)
    
    # Send summary
    summary_msg = f"📊 *Scan Complete*\n🔍 Analyzed: {analyzed} symbols\n🎯 Signals: {signals_found} new\n⏰ {time.strftime('%H:%M UTC')}"
    tg(summary_msg)

    # Notify trade opportunities
    if trades:
        print(f"🚨 Sending {len(trades)} trade notifications...")
        for trade in trades:
            notify_trade(trade["symbol"], trade)
            time.sleep(1)  # Rate limiting
    else:
        print("😴 No new signals found")

if __name__ == "__main__":
    main()
