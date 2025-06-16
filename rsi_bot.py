"""Enhanced Grid Scanner – v3.2 (Fixed)
================================
Fixes applied:
1. **Fixed Bollinger Bands calculation** - now uses proper rolling window
2. **Added input validation** for API responses and data
3. **Improved error handling** with retries and graceful degradation
4. **Fixed symbol conversion logic** with better validation
5. **Added rate limiting** to prevent API throttling
6. **Improved logging** with more detailed error information
7. **Fixed division by zero** issues in calculations
8. **Added data validation** for price and volume fields
9. **Improved Telegram message formatting** and error handling
10. **Added configuration validation** on startup

Adjust filters easily via `.env`:
```
SCAN_MODE=conservative   # conservative → aggressive → loose
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```
"""

from __future__ import annotations

import os
import requests
import logging
import time
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

# ───────────────────── CONFIG ──────────────────────
PIONEX_API = "https://api.pionex.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SCAN_MODE = os.getenv("SCAN_MODE", "conservative").lower()
TOP_N_RESULTS = int(os.getenv("TOP_N_RESULTS", "10"))
CHUNK_SIZE = 3800  # telegram safe
REQUEST_TIMEOUT = 15
RETRY_COUNT = 3
RATE_LIMIT_DELAY = 0.5  # seconds between API calls

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("grid_scanner.log", mode='a')
    ]
)
logger = logging.getLogger(__name__)

# ─────────────── VALIDATION ──────────────────────

def validate_config():
    """Validate configuration and environment variables"""
    errors = []
    
    if SCAN_MODE not in ["conservative", "aggressive", "loose"]:
        errors.append(f"Invalid SCAN_MODE: {SCAN_MODE}. Must be: conservative, aggressive, or loose")
    
    if TOP_N_RESULTS <= 0:
        errors.append(f"TOP_N_RESULTS must be positive, got: {TOP_N_RESULTS}")
    
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_TOKEN not set - Telegram notifications disabled")
    
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set - Telegram notifications disabled")
    
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)
    
    logger.info(f"Configuration validated - Mode: {SCAN_MODE}, Results: {TOP_N_RESULTS}")

# ─────────────── TELEGRAM HELPERS ──────────────────

def tg_send(text: str) -> bool:
    """Send message to Telegram with improved error handling"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram credentials missing - skipping send")
        return False
    
    if not text.strip():
        logger.warning("Empty message - skipping Telegram send")
        return False
    
    for attempt in range(RETRY_COUNT):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID, 
                "text": text[:4096],  # Telegram message limit
                "parse_mode": "Markdown"
            }
            
            response = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            result = response.json()
            if not result.get("ok", False):
                raise RuntimeError(f"Telegram API error: {result.get('description', 'Unknown error')}")
            
            logger.debug("Telegram message sent successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram send attempt {attempt + 1} failed: {e}")
            if attempt < RETRY_COUNT - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
        except Exception as e:
            logger.error(f"Unexpected error sending to Telegram: {e}")
            break
    
    logger.error("Failed to send Telegram message after all retries")
    return False

# ─────────────── DATA FETCHERS ────────────────────

def make_api_request(url: str, params: Dict = None) -> Dict:
    """Make API request with retry logic"""
    for attempt in range(RETRY_COUNT):
        try:
            time.sleep(RATE_LIMIT_DELAY)  # Rate limiting
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"API request attempt {attempt + 1} failed: {e}")
            if attempt < RETRY_COUNT - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise RuntimeError(f"API request failed after {RETRY_COUNT} attempts: {e}")

def fetch_perp_tickers() -> List[Dict]:
    """Fetch perpetual futures tickers with validation"""
    url = f"{PIONEX_API}/api/v1/market/tickers"
    
    try:
        data = make_api_request(url, {"type": "PERP"})
    except Exception as e:
        raise RuntimeError(f"Failed to fetch tickers: {e}")

    # Validate response structure
    if data.get("code", 0) != 0:
        raise RuntimeError(f"API returned error code: {data.get('code')} - {data.get('message', 'Unknown error')}")
    
    if "data" not in data or "tickers" not in data["data"]:
        raise RuntimeError(f"Unexpected ticker response structure: {list(data.keys())}")
    
    tickers = data["data"]["tickers"]
    if not isinstance(tickers, list):
        raise RuntimeError(f"Expected tickers list, got: {type(tickers)}")
    
    # Validate ticker data
    valid_tickers = []
    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue
        
        required_fields = ["symbol", "close"]
        if not all(field in ticker for field in required_fields):
            logger.warning(f"Ticker missing required fields: {ticker.get('symbol', 'unknown')}")
            continue
        
        try:
            float(ticker["close"])  # Validate price is numeric
            volume = float(ticker.get("turnover", 0))  # Validate volume
            valid_tickers.append(ticker)
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid ticker data for {ticker.get('symbol', 'unknown')}: {e}")
            continue
    
    logger.info(f"Fetched {len(valid_tickers)} valid tickers")
    return valid_tickers

def convert_symbol_for_klines(symbol: str) -> str:
    """Convert perpetual symbol to spot symbol for klines API"""
    if symbol.endswith("_PERP"):
        # Remove _PERP suffix
        base_symbol = symbol.replace("_PERP", "")
        
        # Handle cross-pair perpetuals (e.g., AAVE_ETH_PERP, ADA_BTC_PERP)
        # These likely don't have corresponding spot pairs, skip them
        if "_ETH" in base_symbol or "_BTC" in base_symbol:
            if not base_symbol.endswith("_USDT"):
                # For cross pairs, we'll try to use the base asset with USDT
                # e.g., AAVE_ETH -> AAVE_USDT
                base_asset = base_symbol.split("_")[0]
                return f"{base_asset}_USDT"
        
        # If it already ends with _USDT, use as-is
        if base_symbol.endswith("_USDT"):
            return base_symbol
        
        # Otherwise, add _USDT
        return f"{base_symbol}_USDT"
    
    return symbol

# Cache for invalid symbols to avoid repeated API calls
INVALID_SYMBOLS = set()

def fetch_klines(symbol: str, interval: str = "60M", limit: int = 200) -> Tuple[List[float], List[float], List[float]]:
    """Fetch kline data with improved error handling and conversion"""
    spot_symbol = convert_symbol_for_klines(symbol)
    
    # Skip if we know this symbol is invalid
    if spot_symbol in INVALID_SYMBOLS:
        raise RuntimeError(f"Skipping known invalid symbol: {spot_symbol}")
    
    url = f"{PIONEX_API}/api/v1/market/klines"
    
    logger.debug(f"Fetching klines for {symbol} -> {spot_symbol}")
    
    try:
        data = make_api_request(url, {
            "symbol": spot_symbol, 
            "interval": interval, 
            "limit": limit
        })
    except Exception as e:
        raise RuntimeError(f"Failed to fetch klines for {symbol} (as {spot_symbol}): {e}")
    
    # Validate response
    if data.get("code", 0) != 0:
        error_msg = data.get("message", "Unknown error")
        
        # Cache invalid symbols to avoid future attempts
        if "invalid symbol" in error_msg.lower() or "symbol error" in error_msg.lower():
            INVALID_SYMBOLS.add(spot_symbol)
            if spot_symbol != symbol:
                INVALID_SYMBOLS.add(symbol)
        
        # Try without conversion if original conversion failed
        if ("symbol error" in error_msg.lower() or "invalid symbol" in error_msg.lower()) and spot_symbol != symbol and symbol not in INVALID_SYMBOLS:
            logger.debug(f"Symbol conversion failed for {symbol}, trying original symbol")
            try:
                data = make_api_request(url, {
                    "symbol": symbol, 
                    "interval": interval, 
                    "limit": limit
                })
                if data.get("code", 0) != 0:
                    INVALID_SYMBOLS.add(symbol)
                    raise RuntimeError(f"Klines API error for {symbol}: {data.get('message', 'Unknown error')}")
            except Exception:
                INVALID_SYMBOLS.add(symbol)
                raise RuntimeError(f"Klines API error for {symbol} (tried both {spot_symbol} and {symbol}): {error_msg}")
        else:
            raise RuntimeError(f"Klines API error for {symbol}: {error_msg}")
    
    if "data" not in data or "klines" not in data["data"]:
        raise RuntimeError(f"Invalid klines response structure for {symbol}")
    
    klines = data["data"]["klines"]
    if not klines or len(klines) < 20:  # Need minimum data for Bollinger Bands
        raise RuntimeError(f"Insufficient kline data for {symbol}: {len(klines)} candles")
    
    # Extract and validate price data
    closes, highs, lows = [], [], []
    for i, kline in enumerate(klines):
        try:
            close = float(kline["close"])
            high = float(kline["high"])
            low = float(kline["low"])
            
            # Basic validation
            if not (low <= close <= high):
                logger.warning(f"Invalid price data in kline {i} for {symbol}: L:{low} C:{close} H:{high}")
                continue
            
            closes.append(close)
            highs.append(high)
            lows.append(low)
            
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Error parsing kline {i} for {symbol}: {e}")
            continue
    
    if len(closes) < 20:
        raise RuntimeError(f"Not enough valid price data for {symbol}: {len(closes)} points")
    
    return closes, highs, lows

# ─────────────── TECHNICAL ANALYSIS ──────────────────

def bollinger_bands(prices: List[float], period: int = 20, std_dev: float = 2.0) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate Bollinger Bands with proper validation"""
    if len(prices) < period:
        return None, None, None
    
    try:
        # Use the last 'period' prices for calculation
        recent_prices = prices[-period:]
        
        # Calculate moving average
        sma = np.mean(recent_prices)
        
        # Calculate standard deviation
        std = np.std(recent_prices, ddof=0)  # Population standard deviation
        
        # Calculate bands
        upper_band = sma + (std_dev * std)
        lower_band = sma - (std_dev * std)
        
        # Validation
        if not all(np.isfinite([upper_band, sma, lower_band])):
            return None, None, None
        
        if upper_band <= lower_band:
            return None, None, None
        
        return upper_band, sma, lower_band
        
    except Exception as e:
        logger.error(f"Error calculating Bollinger Bands: {e}")
        return None, None, None

def estimate_cycles_per_day(width_pct: float) -> float:
    """Estimate trading cycles per day based on band width"""
    if width_pct <= 0:
        return 0.0
    
    # Improved cycle estimation based on market volatility
    # Higher volatility = more potential cycles
    base_cycles = width_pct * 2.5 / 100  # Adjusted multiplier
    
    # Apply bounds
    return max(0.1, min(base_cycles, 5.0))  # Reasonable bounds

# ─────────────── FILTER LOGIC ─────────────────────

FILTERS = {
    "conservative": {"width": (5, 15), "vol": 3_000_000, "cycles": 1.0},
    "aggressive":   {"width": (3, 25), "vol": 1_000_000, "cycles": 0.5},
    "loose":        {"width": (2, 30), "vol": 500_000,   "cycles": 0.2},
}

def passes_filters(width_pct: float, volume_24h: float, cycles: float, mode: str) -> Tuple[bool, str]:
    """Check if a symbol passes the filters for the given mode"""
    if mode not in FILTERS:
        return False, f"Unknown filter mode: {mode}"
    
    filter_config = FILTERS[mode]
    
    # Check width
    min_width, max_width = filter_config["width"]
    if not (min_width <= width_pct <= max_width):
        return False, f"Width {width_pct:.2f}% outside range [{min_width}-{max_width}%]"
    
    # Check volume
    min_volume = filter_config["vol"]
    if volume_24h < min_volume:
        return False, f"Volume ${volume_24h/1e6:.1f}M below minimum ${min_volume/1e6:.1f}M"
    
    # Check cycles
    min_cycles = filter_config["cycles"]
    if cycles < min_cycles:
        return False, f"Cycles {cycles:.2f} below minimum {min_cycles}"
    
    return True, "Passed all filters"

# ─────────────── MAIN ANALYSIS ─────────────────────

def analyze_symbol(ticker: Dict) -> Optional[Dict]:
    """Analyze a single symbol and return results if it passes filters"""
    symbol = ticker["symbol"]
    
    try:
        # Extract basic data
        price = float(ticker["close"])
        volume_24h = float(ticker.get("turnover", 0))
        
        # Fetch price history
        closes, highs, lows = fetch_klines(symbol, "60M", 200)
        
        # Calculate Bollinger Bands
        upper_band, middle_band, lower_band = bollinger_bands(closes)
        
        if middle_band is None:
            logger.info(f"{symbol}: Unable to calculate Bollinger Bands")
            return None
        
        # Calculate metrics
        width_pct = ((upper_band - lower_band) / middle_band) * 100
        cycles = estimate_cycles_per_day(width_pct)
        
        # Validate calculations
        if not all(np.isfinite([width_pct, cycles])):
            logger.warning(f"{symbol}: Invalid calculations (width: {width_pct}, cycles: {cycles})")
            return None
        
        return {
            "symbol": symbol,
            "price": price,
            "volume_24h": volume_24h,
            "lower_band": lower_band,
            "upper_band": upper_band,
            "middle_band": middle_band,
            "width_pct": round(width_pct, 2),
            "cycles": round(cycles, 2),
        }
        
    except Exception as e:
        logger.info(f"{symbol}: Analysis failed - {e}")
        return None

def analyze_mode(mode: str) -> List[Dict]:
    """Analyze all symbols for a given filter mode"""
    logger.info(f"Starting analysis in {mode} mode...")
    
    try:
        tickers = fetch_perp_tickers()
    except Exception as e:
        logger.error(f"Failed to fetch tickers: {e}")
        return []
    
    results = []
    total_tickers = len(tickers)
    
    for i, ticker in enumerate(tickers, 1):
        symbol = ticker["symbol"]
        logger.debug(f"Processing {symbol} ({i}/{total_tickers})")
        
        # Analyze symbol
        analysis = analyze_symbol(ticker)
        if analysis is None:
            continue
        
        # Check filters
        passes, reason = passes_filters(
            analysis["width_pct"], 
            analysis["volume_24h"], 
            analysis["cycles"], 
            mode
        )
        
        if passes:
            results.append(analysis)
            logger.info(f"{symbol}: PASS - Width: {analysis['width_pct']}%, "
                       f"Volume: ${analysis['volume_24h']/1e6:.1f}M, "
                       f"Cycles: {analysis['cycles']}")
        else:
            logger.debug(f"{symbol}: REJECT - {reason}")
    
    # Sort by cycles (descending)
    results.sort(key=lambda x: x["cycles"], reverse=True)
    
    logger.info(f"Analysis complete: {len(results)} symbols passed {mode} filters")
    return results

# ─────────────── FORMATTING & OUTPUT ─────────────────────

def format_result(result: Dict) -> str:
    """Format a single result for display"""
    return (
        f"*{result['symbol']}*  |  {result['width_pct']}% width  |  {result['cycles']} cycles/day\n"
        f"Price: `${result['price']:.4f}`\n"
        f"Range: `${result['lower_band']:.4f}` – `${result['upper_band']:.4f}`\n"
        f"Volume: `${result['volume_24h']/1e6:.1f}M USDT`\n"
        f"Strategy: Grid Trading (10x leverage)\n"
        "─────────────────────────────\n"
    )

def send_results_chunked(results: List[Dict], mode: str):
    """Send results to Telegram in chunks"""
    if not results:
        return False
    
    # Format all results
    formatted_messages = [format_result(result) for result in results[:TOP_N_RESULTS]]
    
    # Split into chunks
    chunks = []
    current_chunk = ""
    
    for message in formatted_messages:
        if len(current_chunk) + len(message) > CHUNK_SIZE:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = message
        else:
            current_chunk += message
    
    if current_chunk:
        chunks.append(current_chunk)
    
    # Send chunks
    success = True
    for i, chunk in enumerate(chunks, 1):
        header = f"📊 *Grid Bot Opportunities* ({i}/{len(chunks)}) - {mode.title()} Mode\n\n"
        if not tg_send(header + chunk):
            success = False
        time.sleep(1.2)  # Rate limiting
    
    return success

# ─────────────── MAIN EXECUTION ─────────────────────

def scan_with_fallback():
    """Main scanning function with fallback modes"""
    validate_config()
    
    # Define mode sequence
    if SCAN_MODE == "conservative":
        modes = ["conservative", "aggressive", "loose"]
    elif SCAN_MODE == "aggressive":
        modes = ["aggressive", "loose"]
    else:
        modes = ["loose"]
    
    for mode in modes:
        logger.info(f"Scanning in {mode} mode...")
        
        try:
            results = analyze_mode(mode)
            
            if results:
                logger.info(f"Found {len(results)} candidates in {mode} mode")
                
                # Send results
                if send_results_chunked(results, mode):
                    # Notify if using fallback mode
                    if mode != SCAN_MODE:
                        fallback_msg = f"⚠️ *Fallback Mode Active*\n\nNo candidates found in `{SCAN_MODE}` mode.\nShowing `{mode}` results instead."
                        tg_send(fallback_msg)
                    return True
                else:
                    logger.error("Failed to send results via Telegram")
            else:
                logger.info(f"No candidates found in {mode} mode")
                
        except Exception as e:
            logger.error(f"Error in {mode} mode analysis: {e}")
            continue
    
    # No results found in any mode
    error_msg = "⚠️ *No Grid Opportunities Found*\n\nNo suitable candidates found in any scanning mode.\nTry again later or adjust filter parameters."
    tg_send(error_msg)
    return False

# ───────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        logger.info("Grid Scanner v3.2 (Fixed) starting...")
        scan_with_fallback()
        logger.info("Grid Scanner completed successfully")
    except KeyboardInterrupt:
        logger.info("Grid Scanner interrupted by user")
    except Exception as e:
        logger.error(f"Grid Scanner failed with unexpected error: {e}")
        sys.exit(1)
