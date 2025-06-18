import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import math

# Configuration
DEBUG_MODE = True  # Changed to True for better debugging
MAX_WORKERS = 10  # Increased for better throughput
REQUEST_DELAY = 0.1  # Reduced delay for faster processing
BATCH_SIZE = 30  # Increased batch size
TELEGRAM_MAX_LENGTH = 4000  # Leave some buffer for Telegram's 4096 limit
MAX_SIGNALS_PER_MESSAGE = 50  # Much higher limit for signals per message

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
                    print(f"❌ HTTP {response.status_code} for {params.get('symbol', 'unknown')}: {response.text[:100]}")
                return None
                
        except requests.exceptions.Timeout:
            wait_time = min(2 ** attempt, 30)
            if DEBUG_MODE:
                print(f"⏰ Timeout for {params.get('symbol', 'unknown')}, retrying in {wait_time}s (attempt {attempt + 1})")
            time.sleep(wait_time)
            continue
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                if DEBUG_MODE:
                    print(f"❌ Request failed after {max_retries} attempts for {params.get('symbol', 'unknown')}: {e}")
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
            if DEBUG_MODE:
                print(f"❌ No response for {sym}")
            return []
        
        data = response.json()
        if DEBUG_MODE and 'error' in data:
            print(f"❌ API Error for {sym}: {data.get('error', 'Unknown error')}")
            
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
        
        if DEBUG_MODE and len(closes) < 20:
            print(f"⚠️  {sym}: Only {len(closes)} closes (need 20+)")
        
        return closes
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"❌ Error fetching {sym}: {e}")
        return []

def calc_rsi(symbol, closes, period=14):
    """Calculate RSI with error handling."""
    try:
        if len(closes) < period + 1:
            return None
        
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [max(delta, 0) for delta in deltas]
        losses = [abs(min(delta, 0)) for delta in deltas]
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return round(rsi, 2)
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"❌ RSI calculation error for {symbol}: {e}")
        return None

def analyze_symbol_batch(symbols_batch):
    """Analyze a batch of symbols sequentially to control rate limits."""
    results = []
    
    for sym in symbols_batch:
        try:
            if DEBUG_MODE:
                print(f"🔍 Analyzing {sym}...")
                
            closes = fetch_closes_safe(sym, "5M", 100)
            if len(closes) < 20:
                if DEBUG_MODE:
                    print(f"⚠️  {sym}: Insufficient data ({len(closes)} closes)")
                continue
            
            rsi = calc_rsi(sym, closes)
            if rsi is None:
                if DEBUG_MODE:
                    print(f"⚠️  {sym}: RSI calculation failed")
                continue
            
            # Calculate volatility
            recent_closes = closes[-50:] if len(closes) >= 50 else closes
            low, high, current = min(recent_closes), max(recent_closes), closes[-1]
            
            volatility = ((high - low) / current) * 100 if high != low else 0
            
            # Signal detection (you need to define these constants)
            VOL_THRESHOLD = 5.0  # Add your volatility threshold
            RSI_OVERSOLD = 30    # Add your RSI oversold level
            RSI_OVERBOUGHT = 70  # Add your RSI overbought level
            
            if volatility >= VOL_THRESHOLD:
                if rsi <= RSI_OVERSOLD:  # Long signal
                    signal = {
                        "symbol": sym, 
                        "zone": "LONG", 
                        "rsi": rsi, 
                        "vol": volatility, 
                        "price": current
                    }
                    results.append(signal)
                    if DEBUG_MODE:
                        print(f"🟢 {sym}: LONG signal (RSI: {rsi}, Vol: {volatility:.2f}%)")
                        
                elif rsi >= RSI_OVERBOUGHT:  # Short signal  
                    signal = {
                        "symbol": sym, 
                        "zone": "SHORT", 
                        "rsi": rsi, 
                        "vol": volatility, 
                        "price": current
                    }
                    results.append(signal)
                    if DEBUG_MODE:
                        print(f"🔴 {sym}: SHORT signal (RSI: {rsi}, Vol: {volatility:.2f}%)")
            
        except Exception as e:
            if DEBUG_MODE:
                print(f"❌ Error analyzing {sym}: {e}")
            continue
    
    return results

def send_telegram_optimized(signals):
    """Send signals to Telegram using maximum message length efficiently."""
    if not signals:
        return
    
    try:
        messages = []
        current_message = ""
        signals_in_current = 0
        
        # Header for first message
        header = "🚨 RSI SIGNALS DETECTED\n" + "=" * 30 + "\n"
        current_message = header
        
        for i, signal in enumerate(signals):
            # Compact signal format to fit more signals per message
            signal_text = (
                f"{signal['symbol']} | {signal['zone']} | "
                f"RSI:{signal['rsi']:.1f} | Vol:{signal['vol']:.1f}% | "
                f"${signal['price']:.4f}\n"
            )
            
            # Check if adding this signal would exceed limits
            would_exceed_length = len(current_message + signal_text) > TELEGRAM_MAX_LENGTH
            would_exceed_count = signals_in_current >= MAX_SIGNALS_PER_MESSAGE
            
            if would_exceed_length or would_exceed_count:
                # Finalize current message
                if current_message.strip():
                    current_message += f"\n📊 {signals_in_current} signals in this batch"
                    messages.append(current_message)
                
                # Start new message
                batch_num = len(messages) + 1
                header = f"🚨 RSI SIGNALS (Batch {batch_num})\n" + "=" * 30 + "\n"
                current_message = header + signal_text
                signals_in_current = 1
            else:
                current_message += signal_text
                signals_in_current += 1
        
        # Add final message if it has content
        if current_message.strip() and signals_in_current > 0:
            current_message += f"\n📊 {signals_in_current} signals in this batch"
            messages.append(current_message)
        
        # Send all messages
        print(f"📱 Sending {len(signals)} signals in {len(messages)} Telegram messages...")
        
        for i, message in enumerate(messages, 1):
            success = send_telegram(message)
            if success:
                print(f"✅ Sent message {i}/{len(messages)}")
            else:
                print(f"❌ Failed to send message {i}/{len(messages)}")
            
            if i < len(messages):  # Don't sleep after last message
                time.sleep(1)  # Small delay between messages
                
    except Exception as e:
        print(f"❌ Error in optimized Telegram sending: {e}")

def main_optimized_scan():
    """Optimized full scan with better debugging and Telegram batching."""
    print("🚀 RSI BOT - OPTIMIZED FULL SCAN")
    print("=" * 60)
    
    # Fetch symbols with rate limiting
    print("📡 Fetching symbols list...")
    symbols = fetch_symbols_safe()
    if not symbols:
        print("❌ Failed to fetch symbols")
        return []
    
    print(f"🎯 Found {len(symbols)} symbols")
    if DEBUG_MODE:
        print(f"📝 First 10 symbols: {symbols[:10]}")
    
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
                else:
                    print(f"📊 Batch {batch_idx + 1}: No signals found")
                
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
    
    # Send results to Telegram with optimized batching
    if all_signals:
        # Send summary first
        summary_msg = (
            f"🚀 RSI SCAN COMPLETE!\n"
            f"⏱️ Time: {elapsed_time/60:.1f} minutes\n"
            f"📊 Analyzed: {processed_count} symbols\n"
            f"🎯 Found: {len(all_signals)} signals\n\n"
            f"Sending detailed signals..."
        )
        send_telegram(summary_msg)
        
        # Send optimized signal batches
        send_telegram_optimized(all_signals)
        
        # Send completion message
        completion_msg = f"✅ All {len(all_signals)} signals sent successfully!"
        send_telegram(completion_msg)
        
    else:
        no_signals_msg = "📊 Scan completed - No signals found matching criteria"
        send_telegram(no_signals_msg)
        print(no_signals_msg)
    
    return all_signals

def fetch_symbols_safe():
    """Safely fetch symbols list with rate limiting and better error handling."""
    try:
        url = f"{API}/market/symbols"
        print(f"🔗 Fetching from: {url}")
        
        response = rate_limited_request(url, {"type": "PERP"})
        
        if not response:
            print("❌ Failed to get symbols response")
            return []
        
        data = response.json()
        
        if DEBUG_MODE:
            print(f"📋 API Response keys: {list(data.keys())}")
        
        symbols_data = data.get("data", {}).get("symbols", [])
        
        if not symbols_data:
            print(f"⚠️  No symbols in response. Full response: {data}")
            return []
        
        symbols = []
        for s in symbols_data:
            if isinstance(s, dict) and s.get("symbol"):
                symbol = s["symbol"].strip()
                if symbol and not symbol.endswith("_"):  # Filter out invalid symbols
                    symbols.append(symbol)
        
        if DEBUG_MODE:
            print(f"✅ Extracted {len(symbols)} valid symbols from {len(symbols_data)} total")
        
        return symbols
        
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

def send_telegram(message):
    """Send message to Telegram with error handling."""
    try:
        # You need to define these constants
        TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"  # Replace with your actual token
        TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"  # Replace with your actual chat ID
        
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or TELEGRAM_TOKEN == "YOUR_BOT_TOKEN":
            print("⚠️  Telegram not configured - add your TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")
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
            print(f"❌ Telegram error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Telegram send error: {e}")
        return False

# You also need to define these constants that were missing:
API = "https://api.pionex.com"  # Replace with actual Pionex API URL
VOL_THRESHOLD = 5.0
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

if __name__ == "__main__":
    # Run the optimized scan
    signals = main_optimized_scan()
