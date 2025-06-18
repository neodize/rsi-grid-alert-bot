import os, json, math, logging, time, requests
from pathlib import Path
import numpy as np

# ── ENV + CONFIG ────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API = "https://api.pionex.com/api/v1"
TOP_N = 100
MIN_NOTIONAL_USD = 1_000_000
SPACING_MIN = 0.3
SPACING_MAX = 1.2
SPACING_TARGET = 0.75
CYCLE_MAX = 2.0
STOP_BUFFER = 0.01
STATE_FILE = Path("active_grids.json")
VOL_THRESHOLD = 2.5

WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE = {"USDT", "USDC", "BUSD", "DAI"}
EXCL = {"LUNA", "LUNC", "USTC"}
ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}
last_trade_time = {}

# Enhanced logging with debug level
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

# ── TELEGRAM ────────────────────────────────────────
def tg(msg):
    logging.info(f"Attempting to send Telegram message: {msg[:100]}...")
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
    is_valid = (u.split("_")[0] not in WRAPPED | STABLE | EXCL and 
                not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))
    logging.debug(f"Symbol {sym} valid: {is_valid}")
    return is_valid

def fetch_symbols():
    logging.info("Fetching symbols...")
    try:
        r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        logging.debug(f"API response structure: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        
        tickers = data.get("data", {}).get("tickers", [])
        logging.info(f"Total tickers received: {len(tickers)}")
        
        pairs = []
        for t in tickers:
            if valid(t["symbol"]) and float(t.get("amount", 0)) > MIN_NOTIONAL_USD:
                pairs.append(t)
                logging.debug(f"Valid pair: {t['symbol']} with volume {t.get('amount', 0)}")
        
        pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
        symbols = [p["symbol"] for p in pairs][:TOP_N]
        logging.info(f"Selected {len(symbols)} symbols: {symbols[:10]}...")
        return symbols
    except Exception as e:
        logging.error(f"Error fetching symbols: {e}")
        return []

# ── FETCH CLOSES WITH LIMIT ─────────────────────────
def fetch_closes(sym, interval="5M", limit=400):
    logging.debug(f"Fetching closes for {sym}, interval={interval}, limit={limit}")
    try:
        r = requests.get(
            f"{API}/market/klines",
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
        
        logging.debug(f"Fetched {len(closes)} closes for {sym}")
        return closes
    except Exception as e:
        logging.error(f"Error fetching closes for {sym}: {e}")
        return []

# ── ANALYSIS FUNCTIONS ──────────────────────────────
def compute_std_dev(closes, period=30):
    result = float(np.std(closes[-period:])) if len(closes) >= period else 0
    logging.debug(f"Computed std_dev: {result}")
    return result

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
    
    logging.debug(f"Cooldown check for {sym}: time_since_last={time_since_last:.0f}s, cooldown={cooldown:.0f}s, should_trigger={should_trigger_result}")
    
    if should_trigger_result:
        last_trade_time[sym] = now
        return True
    return False

def calculate_grids(rng, px, spacing, vol):
    base = rng / (px * spacing / 100)
    if vol < 1.5:
        result = max(4, min(200, math.floor(base / 2)))
    else:
        result = max(10, min(200, math.floor(base)))
    logging.debug(f"Calculated grids: {result}")
    return result

def grid_type_hint(rng_pct, vol):
    if rng_pct < 1.5 and vol < 1.2:
        return "Arithmetic"
    return "Geometric"

def money(p):
    return f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"

def score_signal(d):
    score = round(
        d["vol"] * 2 +
        ((200 - d["grids"]) / 200) * 10 +
        ((1.5 - min(d["spacing"], 1.5)) * 15) +
        (1.5 / max(d["cycle"], 0.1)) * 10,
        1
    )
    logging.debug(f"Calculated score for {d['symbol']}: {score}")
    return score

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

# ── UPDATED ANALYSE FUNCTION ─────────────────────────
def analyse(sym, interval="5M", limit=400):
    logging.debug(f"Analyzing {sym} with interval {interval}")
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 60:
        logging.debug(f"Insufficient data for {sym}: {len(closes)} closes")
        return None
    
    low, high = min(closes), max(closes)
    px = closes[-1]
    rng = high - low
    
    logging.debug(f"{sym}: low={low}, high={high}, current={px}, range={rng}")
    
    if rng <= 0 or px == 0:
        logging.debug(f"Invalid range or price for {sym}")
        return None
    
    pos = (px - low) / rng
    logging.debug(f"{sym}: position in range = {pos:.3f}")
    
    # Only trade if price is closer to one extreme
    if 0.25 <= pos <= 0.75:
        logging.debug(f"{sym}: Price too centered in range ({pos:.3f}), skipping")
        return None
    
    std = compute_std_dev(closes)
    vol = rng / px * 100
    vf = max(0.1, vol + std * 100)
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(vf, 1))))
    grids = calculate_grids(rng, px, spacing, vol)
    cycle = round((grids * spacing) / (vf + 1e-9) * 2, 1)
    
    logging.debug(f"{sym}: vol={vol:.2f}%, std={std:.5f}, spacing={spacing:.2f}%, grids={grids}, cycle={cycle}")
    
    if cycle > CYCLE_MAX or cycle <= 0:
        logging.debug(f"{sym}: Invalid cycle {cycle}, skipping")
        return None
    
    # Dynamically adjust range if price is outside the buffer
    if px < low * (1 - STOP_BUFFER) or px > high * (1 + STOP_BUFFER):
        old_low, old_high = low, high
        low = min(px, low * 0.95)
        high = max(px, high * 1.05)
        logging.debug(f"{sym}: Adjusted range from [{old_low}, {old_high}] to [{low}, {high}]")
    
    # Compute additional indicators
    rsi = compute_rsi(closes)
    bb_lower, bb_upper = compute_bollinger_bands(closes)
    macd_line, signal_line, macd_hist = compute_macd(closes)
    
    logging.debug(f"{sym}: RSI={rsi:.1f}, BB=[{bb_lower:.4f}, {bb_upper:.4f}], MACD={macd_line:.4f}")
    
    # Determine current market regime
    regime = regime_type(std, vol)
    
    # Dynamic thresholds
    if regime == "Trending":
        rsi_long_threshold = 35
        rsi_short_threshold = 65
    elif regime == "Sideways":
        rsi_long_threshold = 30
        rsi_short_threshold = 70
    else:  # Normal regime
        rsi_long_threshold = 30
        rsi_short_threshold = 70
    
    # Evaluate combined entry conditions
    macd_confirm_long = macd_line is not None and macd_line > signal_line
    macd_confirm_short = macd_line is not None and macd_line < signal_line
    
    zone_check = None
    if rsi < rsi_long_threshold and bb_lower is not None and px < bb_lower and macd_confirm_long:
        zone_check = "Long"
        logging.debug(f"{sym}: Long signal - RSI:{rsi:.1f}<{rsi_long_threshold}, px:{px:.4f}<BB_lower:{bb_lower:.4f}, MACD_long:{macd_confirm_long}")
    elif rsi > rsi_short_threshold and bb_upper is not None and px > bb_upper and macd_confirm_short:
        zone_check = "Short"
        logging.debug(f"{sym}: Short signal - RSI:{rsi:.1f}>{rsi_short_threshold}, px:{px:.4f}>BB_upper:{bb_upper:.4f}, MACD_short:{macd_confirm_short}")
    else:
        logging.debug(f"{sym}: No signal - RSI:{rsi:.1f}, regime:{regime}, MACD_long:{macd_confirm_long}, MACD_short:{macd_confirm_short}")
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
    logging.debug(f"Scanning {sym} with fallback")
    r60 = analyse(sym, interval="60M", limit=200)
    if not r60:
        logging.debug(f"{sym}: No 60M signal")
        return None
    
    if r60["vol"] >= vol_threshold:
        logging.debug(f"{sym}: High volatility ({r60['vol']}%), checking 5M")
        r5 = analyse(sym, interval="5M", limit=400)
        if r5 and should_trigger(sym, r5["vol"], r5["std"]):
            logging.info(f"{sym}: 5M signal confirmed")
            return r5
        logging.debug(f"{sym}: 5M signal not confirmed or cooldown active")
        return None
    elif should_trigger(sym, r60["vol"], r60["std"]):
        logging.info(f"{sym}: 60M signal confirmed")
        return r60
    
    logging.debug(f"{sym}: 60M signal not confirmed or cooldown active")
    return None

# ── MAIN FUNCTION ───────────────────────────────────
def main():
    logging.info("=== Starting RSI Bot Scan ===")
    
    # Test Telegram connection
    if TG_TOKEN and TG_CHAT_ID:
        tg("🤖 RSI Bot started - scanning for opportunities...")
    
    prev = load_state()
    nxt, scored, stops = {}, [], []
    current_time = time.time()
    
    symbols = fetch_symbols()
    if not symbols:
        logging.error("No symbols fetched, exiting")
        return
    
    logging.info(f"Scanning {len(symbols)} symbols...")
    
    signals_found = 0
    for i, sym in enumerate(symbols):
        logging.debug(f"Processing symbol {i+1}/{len(symbols)}: {sym}")
        
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

    # Check for symbols no longer meeting criteria
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

    # Send new signals
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        buf = ""
        for i, (score, r) in enumerate(scored, 1):
            m = start_msg(r, i)
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
        # Send a status message if no signals found
        if TG_TOKEN and TG_CHAT_ID:
            tg(f"📊 Scan completed - {len(symbols)} symbols checked, no new opportunities found")

    # Send stop alerts
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

if __name__ == "__main__":
    main()
