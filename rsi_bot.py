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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── TELEGRAM ────────────────────────────────────────
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        ).raise_for_status()
    except Exception as e:
        logging.error("Telegram error: %s", e)

# ── SYMBOL FETCHING ─────────────────────────────────
def valid(sym):
    u = sym.upper()
    return (u.split("_")[0] not in WRAPPED | STABLE | EXCL and 
            not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def fetch_symbols():
    r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
    tickers = r.json().get("data", {}).get("tickers", [])
    pairs = [t for t in tickers if valid(t["symbol"]) and float(t.get("amount", 0)) > MIN_NOTIONAL_USD]
    pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
    return [p["symbol"] for p in pairs][:TOP_N]

# ── FETCH CLOSES WITH LIMIT ─────────────────────────
def fetch_closes(sym, interval="5M", limit=400):
    r = requests.get(
        f"{API}/market/klines",
        params={"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"},
        timeout=10
    )
    payload = r.json().get("data", {})
    kl = payload.get("klines") or payload
    closes = []
    for k in kl:
        if isinstance(k, dict) and "close" in k:
            closes.append(float(k["close"]))
        elif isinstance(k, (list, tuple)) and len(k) >= 5:
            closes.append(float(k[4]))
    return closes

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
    if now - last_trade_time.get(sym, 0) >= cooldown:
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
                return json.loads(content) if content else {}
        except json.JSONDecodeError:
            logging.warning("Invalid JSON in %s, returning empty state", STATE_FILE)
            return {}
    return {}

def save_state(d):
    STATE_FILE.write_text(json.dumps(d, indent=2))

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
    closes = fetch_closes(sym, interval="5M", limit=1)  # Fetch latest close
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
        return 50  # Neutral value if insufficient data
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
        return None, None, None  # Not enough data for MACD
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
    # A simple regime classifier based on volatility % and std deviation of prices.
    if vol > 3 or std_dev > 0.015:
        return "Trending"
    elif vol < 1.5 and std_dev < 0.005:
        return "Sideways"
    return "Normal"

# ── UPDATED ANALYSE FUNCTION ─────────────────────────
def analyse(sym, interval="5M", limit=400):
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 60:
        return None
    low, high = min(closes), max(closes)
    px = closes[-1]  # Current price
    rng = high - low
    if rng <= 0 or px == 0:
        return None
    pos = (px - low) / rng
    # Only trade if price is closer to one extreme.
    if 0.25 <= pos <= 0.75:
        return None
    std = compute_std_dev(closes)
    vol = rng / px * 100
    vf = max(0.1, vol + std * 100)  # Prevent division by zero
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(vf, 1))))
    grids = calculate_grids(rng, px, spacing, vol)
    cycle = round((grids * spacing) / (vf + 1e-9) * 2, 1)
    if cycle > CYCLE_MAX or cycle <= 0:
        return None
    # Dynamically adjust range if price is outside the buffer.
    if px < low * (1 - STOP_BUFFER) or px > high * (1 + STOP_BUFFER):
        low = min(px, low * 0.95)
        high = max(px, high * 1.05)
    
    # Compute additional indicators.
    rsi = compute_rsi(closes)
    bb_lower, bb_upper = compute_bollinger_bands(closes)
    macd_line, signal_line, macd_hist = compute_macd(closes)
    
    # Determine current market regime.
    regime = regime_type(std, vol)
    # Dynamic thresholds: For a trending regime, use less extreme RSI thresholds.
    if regime == "Trending":
        rsi_long_threshold = 35
        rsi_short_threshold = 65
    elif regime == "Sideways":
        rsi_long_threshold = 30
        rsi_short_threshold = 70
    else:  # Normal regime.
        rsi_long_threshold = 30
        rsi_short_threshold = 70
    
    # Evaluate combined entry conditions.
    macd_confirm_long = macd_line is not None and macd_line > signal_line
    macd_confirm_short = macd_line is not None and macd_line < signal_line
    zone_check = None
    if rsi < rsi_long_threshold and bb_lower is not None and px < bb_lower and macd_confirm_long:
        zone_check = "Long"
    elif rsi > rsi_short_threshold and bb_upper is not None and px > bb_upper and macd_confirm_short:
        zone_check = "Short"
    else:
        return None  # No sufficient confluence.
    
    return dict(
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

# ── MAIN FUNCTION ───────────────────────────────────
def main():
    prev = load_state()
    nxt, scored, stops = {}, [], []
    current_time = time.time()

    for sym in fetch_symbols():
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

    for gone in set(prev) - set(nxt):
        mid = (prev[gone]["low"] + prev[gone]["high"]) / 2
        stop_message = stop_msg(gone, "No longer meets criteria", {
            "low": prev[gone]["low"],
            "high": prev[gone]["high"],
            "now": mid
        })
        stops.append(stop_message)
        tg(stop_message)

    save_state(nxt)

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

if __name__ == "__main__":
    main()
