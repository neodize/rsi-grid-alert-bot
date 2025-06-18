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

# ── TELEGRAM NOTIFICATION ───────────────────────────
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
def fetch_symbols():
    """Retrieve the top perpetual trading pairs based on volume."""
    r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
    tickers = r.json().get("data", {}).get("tickers", [])
    return [t["symbol"] for t in tickers]

# ── FETCH PRICE DATA ───────────────────────────────────
def fetch_closes(sym, interval="5M", limit=400):
    """Fetch historical closing prices."""
    r = requests.get(f"{API}/market/klines", params={"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"}, timeout=10)
    payload = r.json().get("data", {}).get("klines", [])
    closes = [float(k[4]) for k in payload if isinstance(k, (list, tuple))]
    return closes

# ── RSI CALCULATION ───────────────────────────────────
def fetch_rsi(sym, interval="5M", period=14):
    """Calculate Relative Strength Index (RSI)."""
    closes = fetch_closes(sym, interval)
    if len(closes) < period:
        return None

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, abs(deltas), 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100  # Extremely bullish

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)

# ── BOLLINGER BANDS VALIDATION ─────────────────────────
def fetch_bollinger(sym, interval="5M"):
    """Calculate Bollinger Bands."""
    closes = fetch_closes(sym, interval)
    if len(closes) < 60:
        return None
    mid = np.mean(closes[-20:])
    std_dev = np.std(closes[-20:])
    upper = mid + (std_dev * 2)
    lower = mid - (std_dev * 2)
    return lower, upper

# ── PRICE ANALYSIS ───────────────────────────────────
def analyse(sym, interval="5M", limit=400):
    """Determine optimal price range with RSI filtering."""
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 60:
        return None

    rsi = fetch_rsi(sym, interval)
    if rsi is None:
        return None

    zone = "Short" if rsi < 45 else "Long" if rsi > 55 else None
    if not zone:
        return None  # Avoid weak trends

    boll_result = fetch_bollinger(sym, interval)
    if boll_result:
        boll_lower, boll_upper = boll_result
        low = max(min(closes), boll_lower)
        high = min(max(closes), boll_upper)
    else:
        low, high = min(closes), max(closes)

    px = closes[-1]
    rng = high - low
    if rng <= 0 or px == 0:
        return None

    return dict(
        symbol=sym,
        zone=zone,
        low=low,
        high=high,
        now=px,
        rsi=rsi,
        vol=round(rng / px * 100, 1),
    )

# ── TRADING SIGNAL DETECTION ───────────────────────
def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    """Scan symbol with multiple timeframes."""
    r60 = analyse(sym, interval="60M", limit=200)
    if not r60:
        return None
    if r60["vol"] >= vol_threshold:
        r5 = analyse(sym, interval="5M", limit=400)
        return r5 if r5 else None
    return r60

# ── STATE MANAGEMENT ───────────────────────────────
def load_state():
    """Load bot state."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_state(d):
    """Save bot state."""
    with open(STATE_FILE, 'w') as f:
        json.dump(d, f, indent=2)

# ── NOTIFICATION SYSTEM ───────────────────────────
def notify_trade(sym, data):
    """Send Telegram trade alerts."""
    msg = (f"📢 Trade Alert: {sym}\n"
           f"🌀 Zone: {data['zone']}\n"
           f"📊 RSI: {data['rsi']}\n"
           f"🔢 Volatility: {data['vol']}%\n"
           f"📈 Price Range: {data['low']} – {data['high']}")
    tg(msg)

# ── MAIN FUNCTION ─────────────────────────────────
def main():
    """Execute trading bot logic."""
    prev = load_state()
    nxt, trades = {}, []

    for sym in fetch_symbols():
        res = scan_with_fallback(sym)
        if not res:
            continue

        prev_state = prev.get(sym, {})
        nxt[sym] = {
            "zone": res["zone"],
            "low": res["low"],
            "high": res["high"],
            "rsi": res["rsi"]
        }

        if sym not in prev or prev[sym]["zone"] != res["zone"]:
            trades.append(res)

    save_state(nxt)

    # Notify trade opportunities
    for trade in trades:
        notify_trade(trade["symbol"], trade)

if __name__ == "__main__":
    main()
