import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path

# ── ENV + CONFIG ─────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API = "https://api.pionex.com/api/v1"
STATE_FILE = Path("active_grids.json")

SPACING_MIN = 0.3
SPACING_MAX = 1.2
SPACING_TARGET = 0.75
CYCLE_MAX = 2.0
VOL_THRESHOLD = 2.5
STOP_BUFFER = 0.01  

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── TELEGRAM ─────────────────────────────────────────
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        logging.error("Missing Telegram credentials")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        logging.info("Telegram Response: %s", r.json())
    except Exception as e:
        logging.error("Telegram error: %s", e)

# ── SYMBOL FETCHING ───────────────────────────────────
def valid(sym):
    """Exclude wrapped, stable, and excluded tokens."""
    u = sym.upper()
    return (u.split("_")[0] not in {"WBTC", "WETH", "WSOL", "WBNB", "USDT", "USDC", "BUSD", "DAI", "LUNA", "LUNC", "USTC"} 
            and not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def fetch_symbols():
    """Retrieve the top perpetual trading pairs based on volume."""
    r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
    tickers = r.json().get("data", {}).get("tickers", [])
    pairs = [t for t in tickers if valid(t["symbol"]) and float(t.get("amount", 0)) > 1_000_000]
    pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
    return [p["symbol"] for p in pairs][:100]

# ── FETCH CLOSES & STATISTICAL FUNCTIONS ───────────────
def fetch_closes(sym, interval="5M", limit=400):
    """Fetch historical closing prices for a given symbol."""
    r = requests.get(f"{API}/market/klines", params={"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"}, timeout=10)
    payload = r.json().get("data", {})
    kl = payload.get("klines") or payload
    closes = [float(k["close"]) if isinstance(k, dict) else float(k[4]) for k in kl if isinstance(k, (list, tuple))]
    return closes

def compute_std_dev(closes, period=30):
    """Calculate standard deviation based on recent price movements."""
    return float(np.std(closes[-period:])) if len(closes) >= period else 0

def fetch_bollinger(sym, interval="5M"):
    """Calculate Bollinger Bands for additional range validation."""
    closes = fetch_closes(sym, interval)
    if len(closes) < 60:
        return None
    mid = np.mean(closes[-20:])
    std_dev = np.std(closes[-20:])
    upper = mid + (std_dev * 2)
    lower = mid - (std_dev * 2)
    return lower, upper

# ── PERCENTILE ADJUSTMENT BASED ON LEVERAGE ──────────
def determine_percentiles(leverage):
    """Adjust range width based on trading leverage."""
    if leverage <= 5:
        return 5, 95  # Wider range for low leverage
    elif leverage <= 10:
        return 3, 97  # Balanced range for mid-leverage
    else:
        return 2, 98  # Tighter range for aggressive leverage

# ── PRICE RANGE CALCULATION ──────────────────────────
def analyse(sym, interval="5M", limit=400, leverage=5):
    """Determine optimal price range for grid bot trading."""
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 60:
        return None

    # Adjust percentile range based on leverage level
    low_pct, high_pct = determine_percentiles(leverage)
    low = np.percentile(closes, low_pct)
    high = np.percentile(closes, high_pct)

    px = closes[-1]  # Current price
    rng = high - low
    if rng <= 0 or px == 0:
        return None

    # Ensure current price inclusion
    if px < low:
        low = px
    elif px > high:
        high = px
    rng = high - low

    std = compute_std_dev(closes)
    vol = rng / px * 100
    vf = max(0.1, vol + std * 100)  # Prevent division by zero

    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / vf)))
    grids = max(10, min(200, math.floor(rng / (px * spacing / 100))))
    cycle = round((grids * spacing) / vf * 2, 1)
    if cycle > CYCLE_MAX or cycle <= 0:
        return None

    pos = (px - low) / rng
    zone = "Long" if pos < 0.5 else "Short"

    # Validate with Bollinger Bands
    boll_result = fetch_bollinger(sym, interval)
    if boll_result:
        boll_lower, boll_upper = boll_result
        low = max(low, boll_lower)
        high = min(high, boll_upper)
        rng = high - low  # Adjust range

    logging.info("Analyse %s: low=%.2f, high=%.2f, px=%.2f, pos=%.2f, vol=%.2f, std=%.5f, cycle=%.1f",
                 sym, low, high, px, pos, vol, std, cycle)

    return dict(
        symbol=sym,
        zone=zone,
        low=low,
        high=high,
        now=px,
        grids=grids,
        spacing=round(spacing, 2),
        vol=round(vol, 1),
        std=round(std, 5),
        cycle=cycle
    )

# ── STATE MANAGEMENT FUNCTIONS ──────────────────────
def load_state():
    """Load the current state from the JSON file."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.error("Error loading state file: %s", e)
            return {}
    return {}

def save_state(state):
    """Save the current state to the JSON file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        logging.info("State saved successfully")
    except IOError as e:
        logging.error("Error saving state file: %s", e)

# ── SCORING AND MESSAGE FUNCTIONS ──────────────────────
def score_signal(data):
    """Calculate a score for the trading signal."""
    # Higher volatility and optimal cycle time get better scores
    vol_score = min(data['vol'] / 10, 5)  # Cap at 5 points
    cycle_score = 5 - abs(data['cycle'] - 1.0) * 2  # Optimal cycle around 1.0
    grid_score = min(data['grids'] / 50, 3)  # More grids = better (up to 3 points)
    
    return round(vol_score + cycle_score + grid_score, 2)

def start_msg(data, rank):
    """Generate a start message for a new trading opportunity."""
    return (f"🚀 #{rank} NEW OPPORTUNITY: {data['symbol']}\n"
            f"🎯 Zone: {data['zone']}\n"
            f"📊 Range: {data['low']:.4f} - {data['high']:.4f}\n"
            f"💰 Current: {data['now']:.4f}\n"
            f"🔢 Grids: {data['grids']}\n"
            f"📏 Spacing: {data['spacing']}%\n"
            f"📈 Volatility: {data['vol']}%\n"
            f"⏰ Cycle: {data['cycle']} days\n"
            f"⭐ Score: {score_signal(data)}")

def stop_msg(symbol, reason, data):
    """Generate a stop message for ending a trade."""
    return (f"🛑 STOP: {symbol}\n"
            f"❌ Reason: {reason}\n"
            f"📊 Range was: {data['low']:.4f} - {data['high']:.4f}\n"
            f"💰 Current: {data['now']:.4f}")

def scan_with_fallback(sym):
    """Scan a symbol with fallback intervals if the primary fails."""
    for interval in ["5M", "15M", "1H"]:
        try:
            result = analyse(sym, interval)
            if result and result.get('vol', 0) > VOL_THRESHOLD:
                return result
        except Exception as e:
            logging.warning("Failed to analyze %s with %s interval: %s", sym, interval, e)
            continue
    return None

# ── CYCLE NOTIFICATION FUNCTION ───────────────────────
def check_cycle_notification(start_time, cycle, sym, warned=False):
    """Send a warning before cycle completion if needed."""
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
        tg(f"⚠️ Cycle Warning: {sym}\n"
           f"Time remaining: {remaining_time}\n"
           f"Consider reviewing or stopping the bot.")
        return True
    return False

# ── EXECUTE TRADES & ALERTS ───────────────────────────
def execute_trade(sym, data):
    """Trigger a trade action with notifications."""
    msg = (f"📈 Trade Alert: {sym}\n"
           f"🔵 Entry Zone: {data['zone']}\n"
           f"📊 Price Range: {data['low']} – {data['high']}\n"
           f"🌀 Score: {score_signal(data)}")
    tg(msg)

# ── MAIN FUNCTION ────────────────────────────────────
def main():
    """Execute the main trading cycle and notify based on conditions."""
    prev = load_state()
    nxt, scored, stops = {}, [], []
    current_time = time.time()

    try:
        symbols = fetch_symbols()
        logging.info("Fetched %d symbols to analyze", len(symbols))
    except Exception as e:
        logging.error("Failed to fetch symbols: %s", e)
        return

    for sym in symbols:
        try:
            res = scan_with_fallback(sym)
            if not res:
                continue

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
            else:
                p = prev[sym]
                if p["zone"] != res["zone"]:
                    stops.append(stop_msg(sym, "Trend flip", res))
                elif res["now"] > p["high"] * (1 + STOP_BUFFER) or res["now"] < p["low"] * (1 - STOP_BUFFER):
                    stops.append(stop_msg(sym, "Price exited range", res))

        except Exception as e:
            logging.error("Error processing symbol %s: %s", sym, e)
            continue

    # Handle symbols that no longer meet criteria
    for gone in set(prev) - set(nxt):
        try:
            mid = (prev[gone]["low"] + prev[gone]["high"]) / 2
            stop_message = stop_msg(gone, "No longer meets criteria", {
                "low": prev[gone]["low"],
                "high": prev[gone]["high"],
                "now": mid
            })
            stops.append(stop_message)
            tg(stop_message)  # Immediate Telegram alert
        except Exception as e:
            logging.error("Error handling removed symbol %s: %s", gone, e)

    save_state(nxt)

    # Send new opportunities
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        buf = ""
        for i, (_, r) in enumerate(scored, 1):
            m = start_msg(r, i)
            if len(buf) + len(m) > 3500:
                tg(buf)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            tg(buf)

    # Send stop notifications
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

    logging.info("Analysis complete. New opportunities: %d, Stops: %d", len(scored), len(stops))

if __name__ == "__main__":
    main()
