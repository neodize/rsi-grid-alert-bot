import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Reduce debug output for full scan
DEBUG_MODE = False  # Set to False for less verbose output

def analyze_symbol_optimized(sym):
    """Optimized analysis with minimal debug output."""
    try:
        closes = fetch_closes(sym, "5M", 100)
        if len(closes) < 20:
            return None
        
        rsi = calc_rsi(sym, closes)
        if rsi is None:
            return None
        
        # Calculate volatility
        recent_closes = closes[-50:] if len(closes) >= 50 else closes
        low, high, current = min(recent_closes), max(recent_closes), closes[-1]
        
        volatility = ((high - low) / current) * 100 if high != low else 0
        
        # Signal detection
        if volatility >= VOL_THRESHOLD:
            if rsi <= RSI_OVERBOUGHT:
                return {"symbol": sym, "zone": "Short", "rsi": rsi, "vol": volatility, "price": current}
            elif rsi >= RSI_OVERSOLD:
                return {"symbol": sym, "zone": "Long", "rsi": rsi, "vol": volatility, "price": current}
        
        return None
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"❌ Error analyzing {sym}: {e}")
        return None

def main_parallel():
    """Parallel processing for faster scanning."""
    print("🚀 RSI BOT - PARALLEL FULL SCAN")
    print("=" * 60)
    
    symbols = fetch_symbols()
    if not symbols:
        return
    
    print(f"🎯 Analyzing {len(symbols)} symbols in parallel...")
    
    signals = []
    analyzed_count = 0
    
    # Use ThreadPoolExecutor for parallel API calls
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all tasks
        future_to_symbol = {executor.submit(analyze_symbol_optimized, sym): sym 
                           for sym in symbols}
        
        # Process results as they complete
        for future in as_completed(future_to_symbol):
            sym = future_to_symbol[future]
            analyzed_count += 1
            
            # Progress update
            if analyzed_count % 50 == 0:
                print(f"📈 Progress: {analyzed_count}/{len(symbols)} ({analyzed_count/len(symbols)*100:.1f}%)")
            
            try:
                result = future.result()
                if result:
                    signals.append(result)
                    print(f"🚨 SIGNAL: {result['symbol']} - {result['zone']}")
            except Exception as e:
                if DEBUG_MODE:
                    print(f"❌ Error processing {sym}: {e}")
    
    # Final results
    print(f"\n📊 PARALLEL SCAN COMPLETE")
    print(f"✅ Analyzed: {analyzed_count}/{len(symbols)}")
    print(f"🎯 Signals: {len(signals)}")
    
    if signals:
        print(f"\n🚨 ALL SIGNALS:")
        for s in signals:
            print(f"  • {s['symbol']}: {s['zone']} | RSI: {s['rsi']:.1f} | Vol: {s['vol']:.2f}%")
    
    return signals

# Add rate limiting to avoid API limits
def fetch_closes_with_retry(sym, interval="5M", limit=100, max_retries=3):
    """Fetch with retry logic and rate limiting."""
    for attempt in range(max_retries):
        try:
            # Small delay between requests
            time.sleep(0.1)  
            
            url = f"{API}/market/klines"
            params = {"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"}
            r = requests.get(url, params=params, timeout=15)
            
            if r.status_code == 429:  # Rate limited
                wait_time = 2 ** attempt  # Exponential backoff
                print(f"⏳ Rate limited, waiting {wait_time}s for {sym}")
                time.sleep(wait_time)
                continue
                
            if r.status_code != 200:
                return []
            
            data = r.json()
            klines = data.get("data", {}).get("klines", [])
            
            closes = []
            for k in klines:
                if isinstance(k, dict) and 'close' in k:
                    close = float(k['close'])
                    if close > 0:
                        closes.append(close)
            
            return closes
            
        except Exception as e:
            if attempt == max_retries - 1:
                if DEBUG_MODE:
                    print(f"❌ Final attempt failed for {sym}: {e}")
                return []
            time.sleep(1)
    
    return []
