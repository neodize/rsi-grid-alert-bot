import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import math

# Configuration
DEBUG_MODE = False
MAX_WORKERS = 5  # Reduced to respect rate limits
REQUEST_DELAY = 0.2  # Minimum delay between requests
BATCH_SIZE = 20  # Symbols to process in each batch
TELEGRAM_MAX_LENGTH = 4096  # Telegram message character limit
TELEGRAM_BATCH_SIZE = 10  # Max signals per telegram message

# Rate limiting
request_lock = Lock()
last_request_time = 0

def rate_limited_request(url, params, timeout=15, max_retries=5):
    """Make rate-limited API requests with exponential backoff."""
    global last_request_time
    
    for attempt in range(max_retries):
        try:
            # Rate limiting with lock
            with request_lock:
                current_time = time.time()
                time_since_last = current_time - last_request_time
                if time_since_last < REQUEST_DELAY:
                    time.sleep(REQUEST_DELAY - time_since_last)
                last_request_time = time.time()
            
            response = requests.get(url, params=params, timeout=timeout)
            
            if response.status_code == 200:
                return response
            elif response.status_code == 429:  # Rate limited
                wait_time = min(2 ** attempt, 60)  # Max 60 seconds
                print(f"⏳ Rate limited, waiting {wait_time}s (attempt {attempt + 1})")
                time.sleep(wait_time)
                continue
            elif response.status_code in [500, 502, 503, 504]:  # Server errors
                wait_time = min(2 ** attempt, 30)
                print(f"🔄 Server error {response.status_code}, retrying in {wait_time}s")
                time.sleep(wait_time)
                continue
            else:
                if DEBUG_MODE:
                    print(f"❌ HTTP {response.status_code}: {response.text[:100]}")
                return None
                
        except requests.exceptions.Timeout:
            wait_time = min(2 ** attempt, 30)
            print(f"⏰ Timeout, retrying in {wait_time}s (attempt {attempt + 1})")
            time.sleep(wait_time)
            continue
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                if DEBUG_MODE:
                    print(f"❌ Request failed after {max_retries} attempts: {e}")
                return None
            time.sleep(2 ** attempt)
    
    return None

def fetch_closes_safe(sym, interval="5M", limit=100):
    """Safely fetch closes with proper rate limiting."""
    try:
        url = f"{API}/market/klines"
        params = {"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"}
        
        response = rate_limited_request(url, params)
        if not response:
            return []
        
        data = response.json()
        klines = data.get("data", {}).get("klines", [])
        
        closes = []
        for k in klines:
            if isinstance(k, dict) and 'close' in k:
                try:
                    close = float(k['close'])
                    if close > 0:
                        closes.append(close)
                except (ValueError, TypeError):
                    continue
        
        return closes
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"❌ Error fetching {sym}: {e}")
        return []

def analyze_symbol_batch(symbols_batch):
    """Analyze a batch of symbols sequentially to control rate limits."""
    results = []
    
    for sym in symbols_batch:
        try:
            closes = fetch_closes_safe(sym, "5M", 100)
            if len(closes) < 20:
                continue
            
            rsi = calc_rsi(sym, closes)
            if rsi is None:
                continue
            
            # Calculate volatility
            recent_closes = closes[-50:] if len(closes) >= 50 else closes
            low, high, current = min(recent_closes), max(recent_closes), closes[-1]
            
            volatility = ((high - low) / current) * 100 if high != low else 0
            
            # Signal detection
            if volatility >= VOL_THRESHOLD:
                if rsi <= RSI_OVERSOLD:  # Long signal
                    results.append({
                        "symbol": sym, 
                        "zone": "Long", 
                        "rsi": rsi, 
                        "vol": volatility, 
                        "price": current
                    })
                elif rsi >= RSI_OVERBOUGHT:  # Short signal  
                    results.append({
                        "symbol": sym, 
                        "zone": "Short", 
                        "rsi": rsi, 
                        "vol": volatility, 
                        "price": current
                    })
            
        except Exception as e:
            if DEBUG_MODE:
                print(f"❌ Error analyzing {sym}: {e}")
            continue
    
    return results

def send_telegram_batch(signals_batch, batch_num, total_batches):
    """Send a batch of signals to Telegram in a single message."""
    if not signals_batch:
        return
    
    try:
        # Create message header
        message = f"🚨 RSI SIGNALS - Batch {batch_num}/{total_batches}\n"
        message += "=" * 35 + "\n\n"
        
        # Add signals
        for signal in signals_batch:
            signal_text = (
                f"📊 {signal['symbol']}\n"
                f"🎯 {signal['zone']} Signal\n"
                f"📈 RSI: {signal['rsi']:.1f}\n"
                f"🔥 Vol: {signal['vol']:.2f}%\n"
                f"💰 Price: ${signal['price']:.4f}\n"
                f"{'-' * 25}\n"
            )
            
            # Check if adding this signal would exceed Telegram limit
            if len(message + signal_text) > TELEGRAM_MAX_LENGTH:
                # Send current message and start new one
                send_telegram(message)
                message = f"🚨 RSI SIGNALS - Batch {batch_num}/{total_batches} (cont.)\n"
                message += "=" * 35 + "\n\n"
            
            message += signal_text
        
        # Send final message
        if len(message.strip()) > 50:  # Only send if has content
            send_telegram(message)
            
    except Exception as e:
        print(f"❌ Error sending Telegram batch: {e}")

def main_optimized_scan():
    """Optimized full scan with proper rate limiting and batched Telegram."""
    print("🚀 RSI BOT - OPTIMIZED FULL SCAN")
    print("=" * 60)
    
    # Fetch symbols with rate limiting
    print("📡 Fetching symbols list...")
    symbols = fetch_symbols_safe()
    if not symbols:
        print("❌ Failed to fetch symbols")
        return []
    
    print(f"🎯 Found {len(symbols)} symbols")
    print(f"⚙️  Using {MAX_WORKERS} workers with {REQUEST_DELAY}s delay")
    
    # Split symbols into batches for controlled processing
    symbol_batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
    print(f"📦 Split into {len(symbol_batches)} batches of {BATCH_SIZE} symbols each")
    
    all_signals = []
    processed_count = 0
    start_time = time.time()
    
    # Process batches with thread pool
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit batch jobs
        future_to_batch = {
            executor.submit(analyze_symbol_batch, batch): i 
            for i, batch in enumerate(symbol_batches)
        }
        
        # Process completed batches
        for future in as_completed(future_to_batch):
            batch_idx = future_to_batch[future]
            processed_count += len(symbol_batches[batch_idx])
            
            try:
                batch_signals = future.result()
                if batch_signals:
                    all_signals.extend(batch_signals)
                    print(f"🎯 Batch {batch_idx + 1}: Found {len(batch_signals)} signals")
                
                # Progress update
                progress = (processed_count / len(symbols)) * 100
                elapsed = time.time() - start_time
                eta = (elapsed / processed_count) * (len(symbols) - processed_count) if processed_count > 0 else 0
                
                print(f"📈 Progress: {processed_count}/{len(symbols)} ({progress:.1f}%) | ETA: {eta/60:.1f}min")
                
            except Exception as e:
                print(f"❌ Error processing batch {batch_idx + 1}: {e}")
    
    # Final results
    elapsed_time = time.time() - start_time
    print(f"\n📊 SCAN COMPLETE!")
    print(f"⏱️  Time: {elapsed_time/60:.1f} minutes")
    print(f"✅ Processed: {processed_count}/{len(symbols)} symbols")
    print(f"🚨 Total Signals: {len(all_signals)}")
    
    # Send results to Telegram in batches
    if all_signals:
        print(f"\n📱 Sending {len(all_signals)} signals to Telegram...")
        
        # Split signals into Telegram batches
        telegram_batches = [
            all_signals[i:i + TELEGRAM_BATCH_SIZE] 
            for i in range(0, len(all_signals), TELEGRAM_BATCH_SIZE)
        ]
        
        # Send summary first
        summary_msg = (
            f"🚀 RSI SCAN COMPLETE!\n"
            f"⏱️ Scan Time: {elapsed_time/60:.1f} minutes\n"
            f"📊 Analyzed: {processed_count} symbols\n"
            f"🎯 Found: {len(all_signals)} signals\n"
            f"📱 Sending in {len(telegram_batches)} messages...\n"
        )
        send_telegram(summary_msg)
        
        # Send signal batches
        for i, batch in enumerate(telegram_batches, 1):
            send_telegram_batch(batch, i, len(telegram_batches))
            time.sleep(1)  # Small delay between Telegram messages
        
        # Send completion message
        completion_msg = f"✅ All {len(all_signals)} signals sent successfully!"
        send_telegram(completion_msg)
        
    else:
        no_signals_msg = "📊 Scan completed - No signals found matching criteria"
        send_telegram(no_signals_msg)
        print(no_signals_msg)
    
    return all_signals

def fetch_symbols_safe():
    """Safely fetch symbols list with rate limiting."""
    try:
        url = f"{API}/market/symbols"
        response = rate_limited_request(url, {"type": "PERP"})
        
        if not response:
            return []
        
        data = response.json()
        symbols_data = data.get("data", {}).get("symbols", [])
        
        symbols = []
        for s in symbols_data:
            if isinstance(s, dict) and s.get("symbol"):
                symbol = s["symbol"].strip()
                if symbol and not symbol.endswith("_"):  # Filter out invalid symbols
                    symbols.append(symbol)
        
        return symbols
        
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# Enhanced Telegram sending function
def send_telegram(message):
    """Send message to Telegram with error handling."""
    try:
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print("⚠️  Telegram not configured")
            return False
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"❌ Telegram error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Telegram send error: {e}")
        return False

if __name__ == "__main__":
    # Run the optimized scan
    signals = main_optimized_scan()
