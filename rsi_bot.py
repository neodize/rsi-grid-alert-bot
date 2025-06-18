import os
import json
import math
import logging
import time
import requests
import numpy as np
from pathlib import Path
import sys

# Ensure Python 3.7+ compatibility
if sys.version_info < (3, 7):
    raise RuntimeError("Python 3.7 or higher required")

# ── ENV + CONFIG ────────────────────────────────────
TG_TOKEN = os.getenv("TG_TOKEN", os.getenv("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()

if not TG_TOKEN or not TG_CHAT_ID:
    logging.warning("Telegram token or chat ID not set, notifications disabled")

API = "https://api.pionex.com/api/v1"
MIN_NOTIONAL_USD = 100_000
SPACING_MIN = 0.7
SPACING_MAX = 1.2
SPACING_TARGET = 0.75
CYCLE_MAX = 5.0
STOP_BUFFER = 0.01
STATE_FILE = Path("active_grids.json")
VOL_THRESHOLD = 1.0
GRID_HEIGHT = 0.15
GRIDS_AMOUNT = 21

# RELAXED THRESHOLDS
POSITION_THRESHOLD = 0.25
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 60
REQUIRE_ALL_INDICATORS = False

WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE = {"USDT", "USDC", "BUSD", "DAI"}
EXCL = {"LUNA", "LUNC", "USTC"}
ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}
last_trade_time = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
    logging.FileHandler("grid_trading_bot.log"),
    logging.StreamHandler()
])

# ── TELEGRAM ────────────────────────────────────────
def tg(msg):
    logging.info(f"Sending Telegram message: {msg[:100]}...")
    if not TG_TOKEN or not TG_CHAT_ID:
        logging.warning("Telegram not configured")
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
        logging.error(f"Telegram error: {e}")
        return False

# ── SYMBOL FETCHING ─────────────────────────────────
def valid(sym):
    u = sym.upper()
    return (u.split("_")[0] not in WRAPPED | STABLE | EXCL and 
            not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def fetch_symbols():
    logging.info("Fetching symbols...")
    try:
        r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        tickers = data.get("data", {}).get("tickers", [])
        logging.info(f"Total tickers received: {len(tickers)}")
        pairs = [t for t in tickers if valid(t["symbol"]) and float(t.get("amount", 0)) > MIN_NOTIONAL_USD]
        pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
        symbols = [p["symbol"] for p in pairs]
        logging.info(f"Selected {len(symbols)} symbols")
        return symbols
    except Exception as e:
        logging.error(f"Error fetching symbols: {e}")
        return []

# ── FETCH CLOSES WITH LIMIT ─────────────────────────
def fetch_closes(sym, interval="5M", limit=400):
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

def calculate_grids(rng, px, spacing, vol, use_fixed_grids=False):
    if use_fixed_grids:
        return GRIDS_AMOUNT
    base = rng / (px * spacing / 100)
    if px < 0.01:
        base *= 2
    if vol < 1.0:
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

def compute_atr(sym, closes, period=14):
    try:
        r = requests.get(
            f"{API}/market/klines",
            params={"symbol": sym, "interval": "5M", "limit": period + 1, "type": "PERP"},
            timeout=10
        )
        r.raise_for_status()
        kl = r.json().get("data", {}).get("klines", [])
        if len(kl) < period + 1:
            return None
        trs = []
        for i in range(1, len(kl)):
            high = float(kl[i]["high"])
            low = float(kl[i]["low"])
            prev_close = float(kl[i-1]["close"])
            tr = max(high - low, abs(high - prev_close), abs(prev_close - low))
            trs.append(tr)
        return np.mean(trs)
    except Exception as e:
        logging.error(f"Error computing ATR for {sym}: {e}")
        return None

def simulate_grid_orders(sym, low, high, grids, spacing, px, closes, capital=100, leverage=10):
    orders = []
    interval = (high - low) / (grids - 1) if grids > 1 else (high - low)
    grid_levels = [low + i * interval for i in range(grids)]
    base_currency = sym.split('_')[0]
    effective_capital = capital * leverage
    num_buy_grids = sum(1 for level in grid_levels if level <= px) or 1
    order_size = (effective_capital / num_buy_grids) / px
    atr = compute_atr(sym, closes)
    stop_reason = None
    if atr:
        if px > high + 3 * atr:
            stop_reason = f"Price {money(px)} above upper limit {money(high)} + 3*ATR {money(3*atr)}"
        elif px < low - 3 * atr:
            stop_reason = f"Price {money(px)} below lower limit {money(low)} - 3*ATR {money(3*atr)}"
    if stop_reason:
        logging.info(f"Bot stop triggered for {sym}: {stop_reason}")
        tg(f"🛑 Stop Grid Bot: {sym}\n📉 Reason: {stop_reason}\n📊 Range: {money(low)} – {money(high)}\n💱 Current Price: {money(px)}")
        return [], stop_reason
    for level in grid_levels:
        try:
            if level <= px:
                order = {
                    'symbol': sym,
                    'type': 'limit',
                    'side': 'buy',
                    'amount': order_size,
                    'price': level,
                    'leverage': leverage
                }
                orders.append(order)
                logging.info(f"Simulated buy order for {sym} at {money(level)}: {order_size:.6f} {base_currency} (leverage: {leverage}x)")
            else:
                order = {
                    'symbol': sym,
                    'type': 'limit',
                    'side': 'sell',
                    'amount': order_size,
                    'price': level,
                    'leverage': leverage
                }
                orders.append(order)
                logging.info(f"Simulated sell order for {sym} at {money(level)}: {order_size:.6f} {base_currency} (leverage: {leverage}x)")
        except Exception as e:
            logging.error(f"Error simulating order for {sym} at {money(level)}: {e}")
    return orders, None

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
            f"🌀 Score: {score}")

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

# ── INDICATOR FUNCTIONS ─────────────────────────────
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

# ── ANALYSE FUNCTION ────────────────────────────────
def analyse(sym, interval="5M", limit=400, use_grid_height=True):
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 60:
        return None
    
    px = closes[-1]
    grid_height = 0.15 if px < 0.1 else 0.05
    use_grid_height = use_grid_height and px >= 0.1
    if use_grid_height:
        low = px * (1 - grid_height / 2)
        high = px * (1 + grid_height / 2)
        rng = high - low
    else:
        low = min(closes) * 0.95
        high = max(closes)
        rng = high - low
    
    if rng <= 0 or px == 0:
        return None
    
    pos = (px - low) / rng
    
    if POSITION_THRESHOLD <= pos <= (1 - POSITION_THRESHOLD):
        logging.debug(f"{sym}: Price too centered in range ({pos:.3f}), skipping")
        return None
    
    std = compute_std_dev(closes)
    vol = rng / px * 100
    vf = max(0.1, vol + std * 100)
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(vf, 1))))
    use_fixed_grids = use_grid_height
    grids = calculate_grids(rng, px, spacing, vol, use_fixed_grids)
    cycle = round((grids * spacing) / (vf + 1e-9) * 2, 1)
    
    if cycle > CYCLE_MAX or cycle <= 0:
        return None
    
    if px < low * (1 - STOP_BUFFER) or px > high * (1 + STOP_BUFFER):
        low = min(px, low * 0.95)
        high = max(px, high * 1.05)
    
    rsi = compute_rsi(closes)
    bb_lower, bb_upper = compute_bollinger_bands(closes)
    macd_line, signal_line, macd_hist = compute_macd(closes)
    logging.debug(f"{sym}: RSI={rsi:.1f}, BB={px < bb_lower if bb_lower else False}, MACD={macd_line > signal_line if macd_line else False}")
    
    rsi_long_threshold = RSI_OVERSOLD
    rsi_short_threshold = RSI_OVERBOUGHT
    
    rsi_signal_long = rsi < rsi_long_threshold
    rsi_signal_short = rsi > rsi_short_threshold
    bb_signal_long = bb_lower is not None and px < bb_lower
    bb_signal_short = bb_upper is not None and px > bb_upper
    macd_signal_long = macd_line is not None and macd_line > signal_line
    macd_signal_short = macd_line is not None and macd_line < signal_line
    
    if px < 0.1:
        if rsi_signal_long or bb_signal_long or macd_signal_long:
            zone_check = "Long"
        elif rsi_signal_short or bb_signal_short or macd_signal_short:
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
    
    orders, stop_reason = simulate_grid_orders(sym, low, high, grids, spacing, px, closes, capital=100, leverage=10)
    if stop_reason:
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
        cycle=cycle,
        orders=orders
    )
    
    logging.info(f"Valid signal found for {sym}: {zone_check} zone, vol={vol:.1f}%, score={score_signal(result)}")
    return result

def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    r60 = analyse(sym, interval="60M", limit=200, use_grid_height=True)
    if not r60:
        return None
    
    if r60["vol"] >= vol_threshold:
        r5 = analyse(sym, interval="5M", limit=400, use_grid_height=True)
        if r5 and should_trigger(sym, r5["vol"], r5["std"]):
            return r5
        return None
    elif should_trigger(sym, r60["vol"], r60["std"]):
        return r60
    
    return None

# ── MAIN FUNCTION ───────────────────────────────────
def main():
    logging.info("=== Starting RSI Bot Scan (RELAXED CONDITIONS) ===")
    
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
        time.sleep(0.5)
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
        config_info = (f"💰 Capital: $100 | 📈 Leverage: 10x\n")
        
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

if __name__ == "__main__":
    main()
