#!/usr/bin/env python3
"""
Combined RSI Bot and ATR Price Range Helper

This file includes:
  1. The ATR-based Price Range Helper (non-intrusive add-on)
  2. Your original RSI bot script (kept intact, only wrapped into rsi_bot_main())
  
At runtime, you can choose:
  [1] Run the original RSI Bot scan (unchanged logic)
  [2] Test the ATR Price Range Helper for a token
  
No core logic of your original script is modified.
"""

import os, json, math, logging, time, requests
from pathlib import Path
import numpy as np
import sys

# =============================================================================
#                     ATR-Based Price Range Helper Module
# =============================================================================

# Configure logging (this configuration is shared)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Pionex API endpoint for fetching candles (to match your original script)
API = "https://api.pionex.com/api/v1"

def fetch_ohlcv(symbol, interval="5M", limit=100):
    """
    Fetch OHLCV data for the given token symbol using the Pionex API.
    Returns a list of candles (dicts with keys: open, high, low, close, volume).
    """
    url = f"{API}/market/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
        "type": "PERP"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logging.error("Error fetching OHLCV data: %s", e)
        sys.exit(1)
    
    data = response.json().get("data", {})
    # The API may return a dict with "klines" or a raw list
    klines = data.get("klines") if isinstance(data, dict) else data
    ohlcv = []
    for k in klines:
        if isinstance(k, dict) and "close" in k:
            candle = {
                "open": float(k["open"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "close": float(k["close"]),
                "volume": float(k.get("volume", 0))
            }
        elif isinstance(k, (list, tuple)) and len(k) >= 5:
            candle = {
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]) if len(k) > 5 else 0
            }
        else:
            continue
        ohlcv.append(candle)
    
    if not ohlcv:
        logging.error("No valid OHLCV data received.")
        sys.exit(1)
    return ohlcv

def calculate_atr(ohlcv, period=14):
    """
    Calculate the Average True Range (ATR) on the provided OHLCV data.
    
    ATR is computed by:
      TR = max[(High - Low), abs(High - Previous Close), abs(Low - Previous Close)]
      ATR = Mean of the last `period` TR calculations.
    """
    if len(ohlcv) < period + 1:
        logging.error("Not enough data to calculate ATR. Need at least %d candles.", period + 1)
        sys.exit(1)
    
    tr_values = []
    for i in range(1, len(ohlcv)):
        current = ohlcv[i]
        prev = ohlcv[i - 1]
        high_low = current["high"] - current["low"]
        high_prev = abs(current["high"] - prev["close"])
        low_prev = abs(current["low"] - prev["close"])
        tr = max(high_low, high_prev, low_prev)
        tr_values.append(tr)
    
    atr = np.mean(tr_values[-period:])
    return atr

def determine_price_range(symbol, interval="5M", atr_period=14, atr_multiplier=2.0):
    """
    Determine a volatility-adjusted price range using ATR.
    
    Uses the last candle's close as the center, and computes:
       Lower Bound = current_price - (atr_multiplier * ATR)
       Upper Bound = current_price + (atr_multiplier * ATR)
       
    Returns a dict with keys: symbol, current_price, atr, lower_bound, upper_bound.
    """
    logging.info("Calculating price range for %s using interval %s", symbol, interval)
    candles = fetch_ohlcv(symbol, interval=interval, limit=atr_period+50)
    atr = calculate_atr(candles, period=atr_period)
    current_price = candles[-1]["close"]
    lower_bound = current_price - atr_multiplier * atr
    upper_bound = current_price + atr_multiplier * atr
    
    return {
        "symbol": symbol,
        "current_price": current_price,
        "atr": atr,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound
    }

def format_currency(val):
    """
    Format numeric value into a currency string (consistent with your bot's style).
    """
    if val < 0.1:
        return f"${val:.8f}"
    elif val < 1:
        return f"${val:,.4f}"
    else:
        return f"${val:,.2f}"

def print_price_range(range_data):
    """
    Print the ATR-based price range in a formatted style.
    """
    symbol = range_data["symbol"]
    current_price = format_currency(range_data["current_price"])
    atr = range_data["atr"]
    lower_bound = format_currency(range_data["lower_bound"])
    upper_bound = format_currency(range_data["upper_bound"])
    
    print("========================================")
    print(f"Token: {symbol}")
    print(f"Current Price: {current_price}")
    print(f"ATR (period): {atr:.4f}")
    print("Suggested ATR-Based Price Range:")
    print(f"  Lower Bound: {lower_bound}")
    print(f"  Upper Bound: {upper_bound}")
    print("========================================")


# =============================================================================
#                     Original RSI Bot Script (Untouched)
# =============================================================================

# ── ENV + CONFIG ────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API_ORIG = "https://api.pionex.com/api/v1"
TOP_N = 100
MIN_NOTIONAL_USD = 1_000_000
SPACING_MIN = 0.3
SPACING_MAX = 1.2
SPACING_TARGET = 0.75
CYCLE_MAX = 2.0
STOP_BUFFER = 0.01
STATE_FILE = Path("active_grids.json")
VOL_THRESHOLD = 2.5

# RELAXED THRESHOLDS FOR TESTING
POSITION_THRESHOLD = 0.4  # Relaxed from 0.25/0.75 to 0.4/0.6
RSI_OVERSOLD = 40         # Relaxed from 30/35
RSI_OVERBOUGHT = 60       # Relaxed from 65/70
REQUIRE_ALL_INDICATORS = False  # Allow 2 out of 3 indicators instead of all 3

WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE = {"USDT", "USDC", "BUSD", "DAI"}
EXCL = {"LUNA", "LUNC", "USTC"}
ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}
last_trade_time = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── TELEGRAM ────────────────────────────────────────
def tg(msg):
    logging.info(f"Sending Telegram message: {msg[:100]}...")
    if not TG_TOKEN or not TG_CHAT_ID:
        logging.warning("Telegram token or chat ID not configured")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        response.raise_for_status()
        logging.info("Telegram message sent successfully")
        return True
    except Exception as e:
        logging.error("Telegram error: %s", e)
        return False

# ── SYMBOL FETCHING ─────────────────────────────────
def valid(sym):
    u = sym.upper()
    return (u.split("_")[0] not in WRAPPED | STABLE | EXCL and 
            not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def fetch_symbols_orig():
    logging.info("Fetching symbols...")
    try:
        r = requests.get(f"{API_ORIG}/market/tickers", params={"type": "PERP"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        
        tickers = data.get("data", {}).get("tickers", [])
        logging.info(f"Total tickers received: {len(tickers)}")
        
        pairs = [t for t in tickers if valid(t["symbol"]) and float(t.get("amount", 0)) > MIN_NOTIONAL_USD]
        pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
        symbols = [p["symbol"] for p in pairs][:TOP_N]
        logging.info(f"Selected {len(symbols)} symbols")
        return symbols
    except Exception as e:
        logging.error(f"Error fetching symbols: {e}")
        return []

# ── FETCH CLOSES WITH LIMIT ─────────────────────────
def fetch_closes(sym, interval="5M", limit=400):
    try:
        r = requests.get(
            f"{API_ORIG}/market/klines",
            params={"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"},
            timeout=10
        )
        r.raise_for_status()
        payload = r.json().get("data", {})
        kl = payload.get("klines") or payload
        
        closes = []
        for k in kl:
            if isinstance(k, dict) and "close" in k:
                closes.append(float(k["close"]))
            elif isinstance(k, (list, tuple)) and len(k) >= 5:
                closes.append(float(k[4]))
        
        return closes
    except Exception as e:
        logging.error(f"Error fetching closes for {sym}: {e}")
        return []

# ── ANALYSIS FUNCTIONS ──────────────────────────────
def compute_std_dev(closes, period=30):
    return float(np.std(closes[-period:])) if len(closes) >= period else 0

def compute_cooldown(vol_pct, std_dev):
    base = 300
    extra = max(0, (vol_pct - 1) + (std_dev - 0.01) * 100) * 60
    return base + extra

def should_trigger(sym, vol_pct, std_dev):
    now = time.time()
    cooldown = compute_cooldown(vol_pct, std_dev)
    last_time = last_trade_time.get(sym, 0)
    time_since_last = now - last_time
    should_trigger_result = time_since_last >= cooldown
    if should_trigger_result:
        last_trade_time[sym] = now
        return True
    return False

def calculate_grids(rng, px, spacing, vol):
    base = rng / (px * spacing / 100)
    if vol < 1.5:
        return max(4, min(200, math.floor(base / 2)))
    else:
        return max(10, min(200, math.floor(base)))

def grid_type_hint(rng_pct, vol):
    if rng_pct < 1.5 and vol < 1.2:
        return "Arithmetic"
    return "Geometric"

def money(p):
    return f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"

def score_signal(d):
    return round(
        d["vol"] * 2 +
        ((200 - d["grids"]) / 200) * 10 +
        ((1.5 - min(d["spacing"], 1.5)) * 15) +
        (1.5 / max(d["cycle"], 0.1)) * 10,
        1
    )

# ── STATE MANAGEMENT ────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                content = f.read().strip()
                state = json.loads(content) if content else {}
                logging.info(f"Loaded state with {len(state)} symbols")
                return state
        except json.JSONDecodeError:
            logging.warning("Invalid JSON in %s, returning empty state", STATE_FILE)
            return {}
    logging.info("No state file found, starting fresh")
    return {}

def save_state(d):
    STATE_FILE.write_text(json.dumps(d, indent=2))
    logging.info(f"Saved state with {len(d)} symbols")

# ── NOTIFICATION FUNCTIONS ──────────────────────────
def start_msg(d, rank=None):
    score = score_signal(d)
    lev = "20x–50x" if d["spacing"] <= 0.5 else "10x–25x" if d["spacing"] <= 0.75 else "5x–15x"
    mode = grid_type_hint((d["high"] - d["low"]) / d["now"] * 100, d["vol"])
    total_seconds = d["cycle"] * 24 * 3600
    days = int(total_seconds // (24 * 3600))
    remaining_seconds = total_seconds % (24 * 3600)
    hours = int(remaining_seconds // 3600)
    minutes = int((remaining_seconds % 3600) // 60)
    cycle_time = f"{days} Day(s) {hours} Hour(s) {minutes} Minute(s)" if days > 0 else f"{hours} Hour(s) {minutes} Minute(s)"
    prefix = f"🥇 Top {rank} — {d['symbol']}" if rank else f"📈 Start Grid Bot: {d['symbol']}"
    
    return (f"{prefix}\n"
            f"📊 Range: {money(d['low'])} – {money(d['high'])}\n"
            f"📈 Entry Zone: {ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: {d['grids']} | 📏 Spacing: {d['spacing']}%\n"
            f"🌪️ Volatility: {d['vol']}% | ⏱️ Cycle: {cycle_time}\n"
            f"🌀 Score: {score} | ⚙️ Leverage Hint: {lev}\n"
            f"🔧 Grid Mode Hint: {mode}")

def stop_msg(sym, reason, info):
    closes = fetch_closes(sym, interval="5M", limit=1)
    now = closes[-1] if closes and closes else (info["low"] + info["high"]) / 2
    return (f"🛑 Exit Alert: {sym}\n"
            f"📉 Reason: {reason}\n"
            f"📊 Range: {money(info['low'])} – {money(info['high'])}\n"
            f"💱 Current Price: {money(now)}")

def check_cycle_notification(start_time, cycle, sym, warned=False):
    if not start_time or not cycle or warned:
        return False
    current_time = time.time()
    elapsed_time = current_time - start_time
    cycle_seconds = cycle * 24 * 3600
    threshold = max(3600, cycle_seconds * 0.1)
    remaining = cycle_seconds - elapsed_time
    if 0 < remaining <= threshold:
        remaining_seconds = remaining
        days = int(remaining_seconds // (24 * 3600))
        remaining_seconds %= (24 * 3600)
        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)
        remaining_time = f"{days} Day(s) {hours} Hour(s) {minutes} Minute(s)" if days > 0 else f"{hours} Hour(s) {minutes} Minute(s)"
        cycle_seconds_total = cycle * 24 * 3600
        days_total = int(cycle_seconds_total // (24 * 3600))
        remaining_seconds_total = cycle_seconds_total % (24 * 3600)
        hours_total = int(remaining_seconds_total // 3600)
        minutes_total = int((remaining_seconds_total % 3600) // 60)
        cycle_time = f"{days_total} Day(s) {hours_total} Hour(s) {minutes_total} Minute(s)" if days_total > 0 else f"{hours_total} Hour(s) {minutes_total} Minute(s)"
        tg(f"⚠️ Cycle Warning: {sym}\n"
           f"Estimated cycle completion: {cycle_time}\n"
           f"Time remaining: {remaining_time}\n"
           f"Consider reviewing or stopping the bot.")
        return True
    return False

# ── ADDITIONAL INDICATOR FUNCTIONS ───────────────────
def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = np.diff(closes)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period or 1e-9
    rs = up / down
    rsi = [100 - 100 / (1 + rs)]
    for delta in deltas[period:]:
        up_val = max(delta, 0)
        down_val = -min(delta, 0)
        up = (up * (period - 1) + up_val) / period
        down = (down * (period - 1) + down_val) / period or 1e-9
        rs = up / down
        rsi.append(100 - 100 / (1 + rs))
    return rsi[-1]

def compute_bollinger_bands(closes, period=20, dev_factor=2):
    if len(closes) < period:
        return None, None
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    lower = sma - dev_factor * std
    upper = sma + dev_factor * std
    return lower, upper

def compute_macd(closes, slow=26, fast=12, signal=9):
    if len(closes) < slow:
        return None, None, None
    ema_fast = np.zeros_like(closes)
    ema_slow = np.zeros_like(closes)
    alpha_fast = 2 / (fast + 1)
    alpha_slow = 2 / (slow + 1)
    ema_fast[0] = closes[0]
    ema_slow[0] = closes[0]
    for i in range(1, len(closes)):
        ema_fast[i] = alpha_fast * closes[i] + (1 - alpha_fast) * ema_fast[i-1]
        ema_slow[i] = alpha_slow * closes[i] + (1 - alpha_slow) * ema_slow[i-1]
    macd_line = ema_fast - ema_slow
    signal_line = np.zeros_like(closes)
    alpha_signal = 2 / (signal + 1)
    signal_line[0] = macd_line[0]
    for i in range(1, len(macd_line)):
        signal_line[i] = alpha_signal * macd_line[i] + (1 - alpha_signal) * signal_line[i-1]
    histogram = macd_line - signal_line
    return macd_line[-1], signal_line[-1], histogram[-1]

def regime_type(std_dev, vol):
    if vol > 3 or std_dev > 0.015:
        return "Trending"
    elif vol < 1.5 and std_dev < 0.005:
        return "Sideways"
    return "Normal"

# ── RELAXED ANALYSE FUNCTION ─────────────────────────
def analyse(sym, interval="5M", limit=400):
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 60:
        return None
    
    low, high = min(closes), max(closes)
    px = closes[-1]
    rng = high - low
    
    if rng <= 0 or px == 0:
        return None
    
    pos = (px - low) / rng
    
    # RELAXED: Allow more centered positions
    if POSITION_THRESHOLD <= pos <= (1 - POSITION_THRESHOLD):
        logging.debug(f"{sym}: Price too centered in range ({pos:.3f}), skipping")
        return None
    
    std = compute_std_dev(closes)
    vol = rng / px * 100
    vf = max(0.1, vol + std * 100)
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(vf, 1))))
    grids = calculate_grids(rng, px, spacing, vol)
    cycle = round((grids * spacing) / (vf + 1e-9) * 2, 1)
    
    if cycle > CYCLE_MAX or cycle <= 0:
        return None
    
    # Dynamically adjust range if price is outside the buffer
    if px < low * (1 - STOP_BUFFER) or px > high * (1 + STOP_BUFFER):
        low = min(px, low * 0.95)
        high = max(px, high * 1.05)
    
    # Compute additional indicators
    rsi = compute_rsi(closes)
    bb_lower, bb_upper = compute_bollinger_bands(closes)
    macd_line, signal_line, macd_hist = compute_macd(closes)
    
    regime = regime_type(std, vol)
    
    rsi_long_threshold = RSI_OVERSOLD
    rsi_short_threshold = RSI_OVERBOUGHT
    
    rsi_signal_long = rsi < rsi_long_threshold
    rsi_signal_short = rsi > rsi_short_threshold
    bb_signal_long = bb_lower is not None and px < bb_lower
    bb_signal_short = bb_upper is not None and px > bb_upper
    macd_signal_long = macd_line is not None and macd_line > signal_line
    macd_signal_short = macd_line is not None and macd_line < signal_line
    
    if REQUIRE_ALL_INDICATORS:
        if rsi_signal_long and bb_signal_long and macd_signal_long:
            zone_check = "Long"
        elif rsi_signal_short and bb_signal_short and macd_signal_short:
            zone_check = "Short"
        else:
            return None
    else:
        long_signals = sum([rsi_signal_long, bb_signal_long, macd_signal_long])
        short_signals = sum([rsi_signal_short, bb_signal_short, macd_signal_short])
        
        if long_signals >= 2:
            zone_check = "Long"
            logging.info(f"{sym}: Long signal - RSI:{rsi_signal_long}, BB:{bb_signal_long}, MACD:{macd_signal_long} ({long_signals}/3)")
        elif short_signals >= 2:
            zone_check = "Short"
            logging.info(f"{sym}: Short signal - RSI:{rsi_signal_short}, BB:{bb_signal_short}, MACD:{macd_signal_short} ({short_signals}/3)")
        else:
            return None
    
    result = dict(
        symbol=sym,
        zone=zone_check,
        low=low,
        high=high,
        now=px,
        grids=grids,
        spacing=round(spacing, 2),
        vol=round(vol, 1),
        std=round(std, 5),
        cycle=cycle
    )
    
    logging.info(f"Valid signal found for {sym}: {zone_check} zone, vol={vol:.1f}%, score={score_signal(result)}")
    return result

def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    r60 = analyse(sym, interval="60M", limit=200)
    if not r60:
        return None
    
    if r60["vol"] >= vol_threshold:
        r5 = analyse(sym, interval="5M", limit=400)
        if r5 and should_trigger(sym, r5["vol"], r5["std"]):
            return r5
        return None
    elif should_trigger(sym, r60["vol"], r60["std"]):
        return r60
    
    return None

# ── ORIGINAL MAIN FUNCTION (wrapped as rsi_bot_main) ─────────────────
def rsi_bot_main():
    logging.info("=== Starting RSI Bot Scan (RELAXED CONDITIONS) ===")
    
    prev = load_state()
    nxt, scored, stops = {}, [], []
    current_time = time.time()
    
    symbols = fetch_symbols_orig()
    if not symbols:
        logging.error("No symbols fetched, exiting")
        return
    
    logging.info(f"Scanning {len(symbols)} symbols...")
    
    signals_found = 0
    for i, sym in enumerate(symbols):        
        res = scan_with_fallback(sym)
        if not res:
            continue
        
        signals_found += 1
        logging.info(f"Signal #{signals_found} found: {sym}")
        
        prev_state = prev.get(sym, {})
        warned = prev_state.get("warned", False)
        start_time = prev_state.get("start_time", current_time)

        if check_cycle_notification(start_time, res["cycle"], sym, warned):
            warned = True

        nxt[sym] = {
            "zone": res["zone"],
            "low": res["low"],
            "high": res["high"],
            "start_time": start_time,
            "warned": warned
        }

        if sym not in prev:
            scored.append((score_signal(res), res))
            logging.info(f"New signal for {sym}: score={score_signal(res)}")
        else:
            p = prev[sym]
            if p["zone"] != res["zone"]:
                stop_msg_text = stop_msg(sym, "Trend flip", res)
                stops.append(stop_msg_text)
                logging.info(f"Trend flip detected for {sym}")
            elif res["now"] > p["high"] * (1 + STOP_BUFFER) or res["now"] < p["low"] * (1 - STOP_BUFFER):
                stop_msg_text = stop_msg(sym, "Price exited range", res)
                stops.append(stop_msg_text)
                logging.info(f"Price exit detected for {sym}")

    for gone in set(prev) - set(nxt):
        mid = (prev[gone]["low"] + prev[gone]["high"]) / 2
        stop_message = stop_msg(gone, "No longer meets criteria", {
            "low": prev[gone]["low"],
            "high": prev[gone]["high"],
            "now": mid
        })
        stops.append(stop_message)
        logging.info(f"Symbol {gone} no longer meets criteria")

    save_state(nxt)
    
    logging.info(f"Scan complete: {signals_found} signals found, {len(scored)} new, {len(stops)} stops")

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        buf = ""
        config_info = (f"📊 Position threshold: {POSITION_THRESHOLD} (was 0.25)\n"
                      f"📈 RSI thresholds: {RSI_OVERSOLD}/{RSI_OVERBOUGHT} (was 30/70)\n"
                      f"🔧 Require all indicators: {REQUIRE_ALL_INDICATORS}\n\n")
        
        for i, (score, r) in enumerate(scored, 1):
            m = start_msg(r, i)
            if i == 1:
                m = config_info + m
            if len(buf) + len(m) > 3500:
                tg(buf)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            tg(buf)
        logging.info(f"Sent {len(scored)} new signals")
    else:
        logging.info("No new signals to send")
        if TG_TOKEN and TG_CHAT_ID:
            tg(f"📊 Scan completed - {len(symbols)} symbols checked, no new opportunities found\n"
               f"⚙️ Try adjusting thresholds if this persists")

    if stops:
        buf = ""
        for m in stops:
            if len(buf) + len(m) > 3500:
                tg(buf)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            tg(buf)
        logging.info(f"Sent {len(stops)} stop alerts")

# =============================================================================
#                     Dispatcher / Combined Main Function
# =============================================================================

def main():
    print("Select Mode:")
    print("1: Run Original RSI Bot Scan")
    print("2: Test ATR-Based Price Range Helper")
    mode = input("Enter 1 or 2: ").strip()

    if mode == "1":
        rsi_bot_main()
    elif mode == "2":
        token = input("Enter token symbol (e.g., NXPCUSDTPERP): ").strip().upper()
        interval = input("Enter candle interval (default '5M'): ").strip() or "5M"
        try:
            atr_period = int(input("Enter ATR period (default 14): ").strip() or "14")
        except ValueError:
            atr_period = 14
        try:
            atr_multiplier = float(input("Enter ATR multiplier (default 2.0): ").strip() or "2.0")
        except ValueError:
            atr_multiplier = 2.0

        range_data = determine_price_range(token, interval=interval, atr_period=atr_period, atr_multiplier=atr_multiplier)
        print_price_range(range_data)
    else:
        print("Invalid selection. Exiting.")

if __name__ == "__main__":
    main()
