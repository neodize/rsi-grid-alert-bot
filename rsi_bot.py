import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path
import sys

# ── ENV + CONFIG ─────────────────────────────────────

TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API = "https://api.pionex.com/api/v1"
STATE_FILE = Path("active_grids.json")

# Telegram message limits
TG_MAX_MESSAGE_LENGTH = 4000  # Telegram limit is 4096, leave some buffer
TG_MAX_SIGNALS_PER_MESSAGE = 10  # Max signals per message to keep readable

# Trading thresholds
VOL_THRESHOLD = 0.5  # 0.5% volatility minimum
RSI_OVERSOLD = 30      
RSI_OVERBOUGHT = 70    
DEBUG_MODE = False  # Set to False for production scanning

print(f"🚀 RSI BOT - FULL SCAN MODE")
print(f"🎯 Thresholds: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT} (Short), RSI≥{RSI_OVERSOLD} (Long)")
sys.stdout.flush()

# ── TELEGRAM NOTIFICATION ───────────────────────────

def tg(msg):
    """Send Telegram message with length checking."""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ Missing Telegram credentials")
        return False
    
    # Split message if too long
    if len(msg) > TG_MAX_MESSAGE_LENGTH:
        print(f"📱 Message too long ({len(msg)} chars), splitting...")
        messages = split_message(msg)
        success = True
        for i, part in enumerate(messages, 1):
            print(f"📱 Sending part {i}/{len(messages)}")
            if not send_single_message(part):
                success = False
            time.sleep(1)  # Rate limiting
        return success
    else:
        return send_single_message(msg)

def send_single_message(msg):
    """Send a single Telegram message."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Telegram error: {e}")
        return False

def split_message(msg):
    """Split long message into smaller parts."""
    parts = []
    lines = msg.split('\n')
    current_part = ""
    
    for line in lines:
        if len(current_part + line + '\n') > TG_MAX_MESSAGE_LENGTH:
            if current_part:
                parts.append(current_part.strip())
                current_part = line + '\n'
            else:
                # Single line too long, force split
                parts.append(line[:TG_MAX_MESSAGE_LENGTH-3] + "...")
        else:
            current_part += line + '\n'
    
    if current_part:
        parts.append(current_part.strip())
    
    return parts

# ── SYMBOL FETCHING ──────────────────────────────────

def fetch_symbols():
    """Get ALL symbols from API."""
    try:
        print("🔍 Fetching ALL symbols from API…")
        sys.stdout.flush()

        r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=15)
        
        if r.status_code != 200:
            print(f"❌ API Error: {r.status_code} - {r.text[:200]}")
            sys.stdout.flush()
            return []
        
        data = r.json()
        tickers = data.get("data", {}).get("tickers", [])
        
        symbols = []
        for t in tickers:
            if isinstance(t, dict) and 'symbol' in t:
                symbols.append(t["symbol"])
        
        print(f"✅ Found {len(symbols)} total symbols to analyze")
        if DEBUG_MODE:
            print(f"📋 First 10 symbols: {symbols[:10]}")
        sys.stdout.flush()
        
        return symbols
        
    except Exception as e:
        print(f"❌ Exception fetching symbols: {e}")
        return []

# ── PRICE DATA FETCHING ──────────────────────────────

def fetch_closes(sym, interval="5M", limit=100):
    """Fetch price closes for symbol."""
    try:
        if DEBUG_MODE:
            print(f"  📥 Fetching {sym} data...")

        r = requests.get(
            f"{API}/market/klines",
            params={"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"},
            timeout=15
        )
        
        if r.status_code != 200:
            if DEBUG_MODE:
                print(f"    ❌ HTTP {r.status_code} for {sym}")
            return []
        
        data = r.json()
        klines = data.get("data", {}).get("klines", [])
        
        if not klines:
            if DEBUG_MODE:
                print(f"    ⚠️  No data for {sym}")
            return []
        
        closes = []
        for k in klines:
            try:
                if isinstance(k, dict):
                    close_str = k.get('close')
                    if close_str:
                        close = float(close_str)
                        if close > 0:
                            closes.append(close)
                elif isinstance(k, (list, tuple)) and len(k) > 4:
                    close = float(k[4])
                    if close > 0:
                        closes.append(close)
            except (ValueError, TypeError):
                continue
        
        if DEBUG_MODE and closes:
            print(f"    ✅ {sym}: {len(closes)} closes, latest: {closes[-1]:.8f}")
        
        return closes
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"  ❌ Error fetching {sym}: {e}")
        return []

# ── RSI CALCULATION ──────────────────────────────────

def calc_rsi(closes, period=14):
    """Calculate RSI efficiently."""
    if len(closes) < period + 5:
        return None

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, abs(deltas), 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

# ── MAIN ANALYSIS ────────────────────────────────────

def analyze_symbol(sym):
    """Analyze symbol for signals."""
    closes = fetch_closes(sym, "5M", 100)
    
    if len(closes) < 20:
        return None

    rsi = calc_rsi(closes)
    if rsi is None:
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

    # Check for signals
    if volatility < VOL_THRESHOLD:
        return None
    
    zone = None
    if rsi <= RSI_OVERBOUGHT:
        zone = "Short"
    elif rsi >= RSI_OVERSOLD:
        zone = "Long"
    
    if zone:
        return {
            "symbol": sym,
            "zone": zone,
            "rsi": rsi,
            "vol": volatility,
            "price": current,
            "low": low,
            "high": high
        }
    
    return None

def format_signals_message(signals, batch_num=None, total_batches=None):
    """Format signals into a consolidated Telegram message."""
    if not signals:
        return "😴 No signals found in this batch"
    
    # Header
    header = "🎯 *Trading Signals Found*"
    if batch_num and total_batches:
        header += f" (Batch {batch_num}/{total_batches})"
    header += f"\n📊 Found {len(signals)} opportunities\n"
    header += f"🎯 Criteria: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT}|≥{RSI_OVERSOLD}\n\n"
    
    # Group signals by type
    long_signals = [s for s in signals if s['zone'] == 'Long']
    short_signals = [s for s in signals if s['zone'] == 'Short']
    
    message = header
    
    # Long signals
    if long_signals:
        message += f"🟢 *LONG SIGNALS ({len(long_signals)})*\n"
        for signal in long_signals:
            line = f"• `{signal['symbol']}` RSI:{signal['rsi']} Vol:{signal['vol']:.2f}% ${signal['price']:.6f}\n"
            message += line
        message += "\n"
    
    # Short signals  
    if short_signals:
        message += f"🔴 *SHORT SIGNALS ({len(short_signals)})*\n"
        for signal in short_signals:
            line = f"• `{signal['symbol']}` RSI:{signal['rsi']} Vol:{signal['vol']:.2f}% ${signal['price']:.6f}\n"
            message += line
        message += "\n"
    
    # Footer
    message += f"⏰ Scan completed at {time.strftime('%H:%M:%S UTC')}"
    
    return message.strip()

# ── MAIN FUNCTION ────────────────────────────────────

def main():
    """Main scanning function with consolidated reporting."""
    print("🚀 RSI BOT - FULL MARKET SCAN")
    print("=" * 60)
    sys.stdout.flush()

    # Send start notification
    start_msg = (f"🚀 *Full Market Scan Started*\n"
                f"🎯 Criteria: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT}|≥{RSI_OVERSOLD}\n"
                f"⏰ Started at {time.strftime('%H:%M:%S UTC')}")
    tg(start_msg)

    # Get all symbols
    symbols = fetch_symbols()
    if not symbols:
        error_msg = "❌ *Scan Failed*\nNo symbols retrieved from API"
        tg(error_msg)
        return

    print(f"🎯 Analyzing {len(symbols)} symbols...")
    sys.stdout.flush()

    # Analyze symbols with progress tracking
    signals = []
    analyzed_count = 0
    errors = 0
    
    total_symbols = len(symbols)
    progress_interval = max(10, total_symbols // 20)  # Show progress every 5%

    for i, sym in enumerate(symbols, 1):
        try:
            result = analyze_symbol(sym)
            analyzed_count += 1
            
            if result:
                signals.append(result)
                print(f"🚨 Signal #{len(signals)}: {sym} - {result['zone']} (RSI: {result['rsi']})")
            
            # Progress updates
            if i % progress_interval == 0:
                progress = (i / total_symbols) * 100
                print(f"📊 Progress: {i}/{total_symbols} ({progress:.1f}%) - Signals: {len(signals)}")
                sys.stdout.flush()
                
        except Exception as e:
            errors += 1
            if DEBUG_MODE:
                print(f"❌ Error analyzing {sym}: {e}")

    # Send consolidated results
    print(f"\n📊 SCAN COMPLETE")
    print(f"🔍 Analyzed: {analyzed_count}/{total_symbols}")
    print(f"🎯 Signals found: {len(signals)}")
    print(f"❌ Errors: {errors}")
    sys.stdout.flush()

    # Send results in batches if needed
    if signals:
        # Split signals into batches to avoid message length limits
        batch_size = TG_MAX_SIGNALS_PER_MESSAGE
        signal_batches = [signals[i:i + batch_size] for i in range(0, len(signals), batch_size)]
        
        print(f"📱 Sending {len(signal_batches)} message batch(es)")
        
        for batch_num, signal_batch in enumerate(signal_batches, 1):
            batch_msg = format_signals_message(
                signal_batch, 
                batch_num if len(signal_batches) > 1 else None,
                len(signal_batches) if len(signal_batches) > 1 else None
            )
            tg(batch_msg)
            if batch_num < len(signal_batches):
                time.sleep(2)  # Rate limiting between batches
    else:
        no_signals_msg = (f"😴 *No Signals Found*\n"
                         f"📊 Scanned {analyzed_count} symbols\n"
                         f"🎯 Criteria: Vol≥{VOL_THRESHOLD}%, RSI≤{RSI_OVERBOUGHT}|≥{RSI_OVERSOLD}\n"
                         f"💡 Try adjusting thresholds if market is quiet")
        tg(no_signals_msg)

    # Final summary
    summary_msg = (f"✅ *Scan Summary*\n"
                  f"📊 Analyzed: {analyzed_count}/{total_symbols}\n"
                  f"🎯 Signals: {len(signals)}\n"
                  f"❌ Errors: {errors}\n"
                  f"⏰ Completed: {time.strftime('%H:%M:%S UTC')}")
    tg(summary_msg)

if __name__ == "__main__":
    main()
