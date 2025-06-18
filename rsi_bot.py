import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import math

# Configuration
DEBUG_MODE = True
MAX_WORKERS = 10  # Restored to original value
REQUEST_DELAY = 0.1  # Back to original faster delay
BATCH_SIZE = 30  # Back to original batch size
TELEGRAM_MAX_LENGTH = 4000
MAX_SIGNALS_PER_MESSAGE = 50

# API Configuration - No API keys needed for public data!
API_BASE = "https://api.pionex.com"

# Telegram Configuration - IMPORTANT: Fill these in!
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"     # Get from @BotFather on Telegram
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"     # Your Telegram chat ID

# Trading Configuration
VOL_THRESHOLD = 5.0
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Rate limiting
request_lock = Lock()
last_request_time = 0

def rate_limited_request(url, params, timeout=15, max_retries=5):
    """Make rate-limited API requests with exponential backoff - no auth needed for public data."""
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

def fetch_symbols_safe():
    """Fetch available trading symbols from Pionex public API."""
    try:
        print("📡 Fetching symbols from Pionex API...")
        
        # Try the correct public endpoints for Pionex
        endpoints_to_try = [
            f"{API_BASE}/api/v1/common/symbols",
            f"{API_BASE}/api/v1/market/symbols", 
            f"{API_BASE}/common/symbols",
            f"{API_BASE}/market/symbols"
        ]
        
        data = None
        successful_endpoint = None
        
        for endpoint_url in endpoints_to_try:
            print(f"🔗 Trying: {endpoint_url}")
            try:
                response = rate_limited_request(endpoint_url, {})
                if response:
                    data = response.json()
                    successful_endpoint = endpoint_url
                    print(f"✅ Success with: {endpoint_url}")
                    break
                else:
                    print(f"❌ Failed: {endpoint_url}")
            except Exception as e:
                print(f"❌ Error with {endpoint_url}: {e}")
                continue
        
        if not data:
            print("❌ All endpoints failed. Using fallback symbol list...")
            # Fallback to common crypto pairs that should exist on most exchanges
            return [
                "BTCUSDT", "ETHUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT",
                "BNBUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "EOSUSDT",
                "TRXUSDT", "ETCUSDT", "XMRUSDT", "DASHUSDT", "ZECUSDT",
                "ATOMUSDT", "ONTUSDT", "IOTAUSDT", "BATUSDT", "VETUSDT",
                "NEOUSDT", "QTUMUSDT", "ICXUSDT", "ZILUSDT", "RVNUSDT"
            ]
        
        if DEBUG_MODE:
            print(f"📋 API Response structure: {list(data.keys())}")
            print(f"📋 Full response sample: {str(data)[:300]}...")
        
        # Parse symbols from response - try different possible formats
        symbols = []
        
        # Try different response structures
        if 'data' in data:
            symbol_data = data['data']
            if isinstance(symbol_data, dict) and 'symbols' in symbol_data:
                symbol_list = symbol_data['symbols']
            elif isinstance(symbol_data, list):
                symbol_list = symbol_data
            else:
                symbol_list = []
        elif 'symbols' in data:
            symbol_list = data['symbols']
        elif isinstance(data, list):
            symbol_list = data
        else:
            symbol_list = []
        
        # Extract symbol names
        for item in symbol_list:
            try:
                if isinstance(item, dict):
                    symbol = item.get('symbol', item.get('name', ''))
                elif isinstance(item, str):
                    symbol = item
                else:
                    continue
                    
                symbol = symbol.strip()
                # Filter for USDT pairs and valid symbols
                if symbol and symbol.endswith('USDT') and len(symbol) > 4:
                    # Check if symbol status is active (if status field exists)
                    if isinstance(item, dict):
                        status = item.get('status', item.get('state', 'TRADING'))
                        if status.upper() in ['TRADING', 'ACTIVE', 'ONLINE']:
                            symbols.append(symbol)
                    else:
                        symbols.append(symbol)
            except Exception as e:
                if DEBUG_MODE:
                    print(f"⚠️ Error parsing symbol item: {e}")
                continue
        
        if not symbols:
            print("⚠️ No symbols parsed from API response, using fallback list")
            return [
                "BTCUSDT", "ETHUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT",
                "BNBUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "EOSUSDT"
            ]
        
        print(f"✅ Found {len(symbols)} trading symbols")
        if DEBUG_MODE:
            print(f"📝 First 10 symbols: {symbols[:10]}")
        
        return symbols
        
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        print("💡 Using fallback symbol list...")
        return ["BTCUSDT", "ETHUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT"]

def fetch_klines_safe(symbol, interval="5m", limit=100):
    """Fetch kline/candlestick data for a symbol using public API."""
    try:
        # Try different possible endpoints for klines/candlestick data
        endpoints_to_try = [
            f"{API_BASE}/api/v1/market/klines",
            f"{API_BASE}/api/v1/klines", 
            f"{API_BASE}/market/klines",
            f"{API_BASE}/klines"
        ]
        
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        
        data = None
        for endpoint_url in endpoints_to_try:
            try:
                response = rate_limited_request(endpoint_url, params)
                if response:
                    data = response.json()
                    break
            except Exception:
                continue
        
        if not data:
            if DEBUG_MODE:
                print(f"❌ No kline data for {symbol} from any endpoint")
            return []
        
        if DEBUG_MODE and 'error' in data:
            print(f"❌ API Error for {symbol}: {data.get('error', 'Unknown error')}")
            return []
        
        # Parse klines from response - handle different response formats
        klines = []
        if 'data' in data:
            if isinstance(data['data'], dict) and 'klines' in data['data']:
                klines = data['data']['klines']
            elif isinstance(data['data'], list):
                klines = data['data']
        elif isinstance(data, list):
            klines = data
        elif 'klines' in data:
            klines = data['klines']
        
        closes = []
        for kline in klines:
            try:
                if isinstance(kline, list) and len(kline) >= 5:
                    # Standard format: [timestamp, open, high, low, close, volume]
                    close_price = float(kline[4])
                elif isinstance(kline, dict):
                    # Object format - try different possible field names
                    close_price = float(kline.get('close', kline.get('c', kline.get('closePrice', 0))))
                else:
                    continue
                    
                if close_price > 0:
                    closes.append(close_price)
            except (ValueError, TypeError, IndexError):
                continue
        
        if DEBUG_MODE and len(closes) < 20:
            print(f"⚠️ {symbol}: Only {len(closes)} closes (need 20+ for RSI)")
        
        return closes
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"❌ Error fetching klines for {symbol}: {e}")
        return []

def calc_rsi(closes, period=14):
    """Calculate RSI indicator."""
    try:
        if len(closes) < period + 1:
            return None
        
        # Calculate price changes
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        # Separate gains and losses
        gains = [max(delta, 0) for delta in deltas]
        losses = [abs(min(delta, 0)) for delta in deltas]
        
        # Calculate average gain and loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        # Calculate RSI
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return round(rsi, 2)
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"❌ RSI calculation error: {e}")
        return None

def analyze_symbol(symbol):
    """Analyze a single symbol for RSI signals."""
    try:
        if DEBUG_MODE:
            print(f"🔍 Analyzing {symbol}...")
        
        # Fetch price data
        closes = fetch_klines_safe(symbol, "5m", 100)
        if len(closes) < 20:
            if DEBUG_MODE:
                print(f"⚠️ {symbol}: Insufficient data ({len(closes)} closes)")
            return None
        
        # Calculate RSI
        rsi = calc_rsi(closes)
        if rsi is None:
            if DEBUG_MODE:
                print(f"⚠️ {symbol}: RSI calculation failed")
            return None
        
        # Calculate volatility
        recent_closes = closes[-50:] if len(closes) >= 50 else closes
        high_price = max(recent_closes)
        low_price = min(recent_closes)
        current_price = closes[-1]
        
        if high_price == low_price:
            volatility = 0
        else:
            volatility = ((high_price - low_price) / current_price) * 100
        
        # Check for signals
        signal = None
        if volatility >= VOL_THRESHOLD:
            if rsi <= RSI_OVERSOLD:
                signal = {
                    "symbol": symbol,
                    "zone": "LONG",
                    "rsi": rsi,
                    "vol": round(volatility, 2),
                    "price": current_price
                }
                if DEBUG_MODE:
                    print(f"🟢 {symbol}: LONG signal (RSI: {rsi}, Vol: {volatility:.2f}%)")
            elif rsi >= RSI_OVERBOUGHT:
                signal = {
                    "symbol": symbol,
                    "zone": "SHORT",
                    "rsi": rsi,
                    "vol": round(volatility, 2),
                    "price": current_price
                }
                if DEBUG_MODE:
                    print(f"🔴 {symbol}: SHORT signal (RSI: {rsi}, Vol: {volatility:.2f}%)")
        
        return signal
        
    except Exception as e:
        if DEBUG_MODE:
            print(f"❌ Error analyzing {symbol}: {e}")
        return None

def send_telegram(message):
    """Send message to Telegram."""
    try:
        if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN" or TELEGRAM_CHAT_ID == "YOUR_CHAT_ID":
            print("⚠️ Telegram not configured - add your TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")
            print(f"📱 Would send: {message[:100]}...")
            return True  # Return True to not block the flow during testing
        
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

def send_telegram_optimized(signals):
    """Send signals to Telegram in optimized batches."""
    if not signals:
        return
    
    try:
        messages = []
        current_message = ""
        signals_in_current = 0
        
        # Header
        header = "🚨 RSI SIGNALS DETECTED\n" + "=" * 30 + "\n"
        current_message = header
        
        for signal in signals:
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
        
        # Add final message
        if current_message.strip() and signals_in_current > 0:
            current_message += f"\n📊 {signals_in_current} signals in this batch"
            messages.append(current_message)
        
        # Send all messages
        print(f"📱 Sending {len(signals)} signals in {len(messages)} messages...")
        
        for i, message in enumerate(messages, 1):
            success = send_telegram(message)
            if success:
                print(f"✅ Sent message {i}/{len(messages)}")
            else:
                print(f"❌ Failed to send message {i}/{len(messages)}")
            
            if i < len(messages):
                time.sleep(1)  # Delay between messages
                
    except Exception as e:
        print(f"❌ Error in Telegram batch sending: {e}")

def main_scan():
    """Main scanning function."""
    print("🚀 RSI BOT - FULL SCAN")
    print("=" * 50)
    
    # Check Telegram configuration
    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN":
        print("⚠️ WARNING: Telegram not configured - signals will be printed only")
    
    # Fetch symbols
    symbols = fetch_symbols_safe()
    if not symbols:
        print("❌ No symbols to analyze")
        return []
    
    print(f"🎯 Analyzing {len(symbols)} symbols")
    print(f"⚙️ Using {MAX_WORKERS} workers with {REQUEST_DELAY}s delay")
    
    # Analyze symbols
    all_signals = []
    processed = 0
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all analysis tasks
        future_to_symbol = {
            executor.submit(analyze_symbol, symbol): symbol 
            for symbol in symbols
        }
        
        # Process completed tasks
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            processed += 1
            
            try:
                signal = future.result()
                if signal:
                    all_signals.append(signal)
                    print(f"🎯 Signal found: {signal['symbol']} - {signal['zone']}")
                
                # Progress update
                if processed % 10 == 0 or processed == len(symbols):
                    progress = (processed / len(symbols)) * 100
                    elapsed = time.time() - start_time
                    eta = (elapsed / processed) * (len(symbols) - processed) if processed > 0 else 0
                    print(f"📈 Progress: {processed}/{len(symbols)} ({progress:.1f}%) | ETA: {eta/60:.1f}min")
                
            except Exception as e:
                print(f"❌ Error processing {symbol}: {e}")
    
    # Results
    elapsed_time = time.time() - start_time
    print(f"\n📊 SCAN COMPLETE!")
    print(f"⏱️ Time: {elapsed_time/60:.1f} minutes")
    print(f"✅ Processed: {processed}/{len(symbols)} symbols")
    print(f"🚨 Total Signals: {len(all_signals)}")
    
    # Send to Telegram
    if all_signals:
        summary_msg = (
            f"🚀 RSI SCAN COMPLETE!\n"
            f"⏱️ Time: {elapsed_time/60:.1f} minutes\n"
            f"📊 Analyzed: {processed} symbols\n"
            f"🎯 Found: {len(all_signals)} signals\n"
        )
        send_telegram(summary_msg)
        send_telegram_optimized(all_signals)
        
        completion_msg = f"✅ All {len(all_signals)} signals sent!"
        send_telegram(completion_msg)
    else:
        no_signals_msg = "📊 Scan completed - No signals found"
        send_telegram(no_signals_msg)
        print(no_signals_msg)
    
    return all_signals

if __name__ == "__main__":
    print("🤖 RSI Trading Bot Starting...")
    print("=" * 50)
    
    print("🔧 Configuration Check:")
    print(f"   Telegram: {'✅ Configured' if TELEGRAM_TOKEN != 'YOUR_BOT_TOKEN' else '❌ Not configured'}")
    print(f"   RSI Oversold: {RSI_OVERSOLD}")
    print(f"   RSI Overbought: {RSI_OVERBOUGHT}")
    print(f"   Volatility Threshold: {VOL_THRESHOLD}%")
    print()
    
    # Run scan
    signals = main_scan()
    
    print(f"\n🏁 Bot finished. Found {len(signals)} signals.")
