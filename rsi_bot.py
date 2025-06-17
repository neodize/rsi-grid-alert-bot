import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path

API = "https://api.pionex.com/api/v1"
STATE_FILE = Path("active_grids.json")

SPACING_MIN = 0.3
SPACING_MAX = 1.2
SPACING_TARGET = 0.75
CYCLE_MAX = 2.0

STOP_BUFFER = 0.01  # Prevent premature stop conditions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def fetch_closes(sym, interval="5M", limit=400):
    r = requests.get(f"{API}/market/klines", params={"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"}, timeout=10)
    payload = r.json().get("data", {})
    kl = payload.get("klines") or payload
    closes = [float(k["close"]) if isinstance(k, dict) else float(k[4]) for k in kl if isinstance(k, (list, tuple))]
    return closes

def compute_std_dev(closes, period=30):
    return float(np.std(closes[-period:])) if len(closes) >= period else 0

def fetch_bollinger(sym, interval="5M"):
    closes = fetch_closes(sym, interval)
    if len(closes) < 60:
        return None
    mid = np.mean(closes[-20:])
    std_dev = np.std(closes[-20:])
    upper = mid + (std_dev * 2)
    lower = mid - (std_dev * 2)
    return lower, upper

def determine_percentiles(leverage):
    if leverage <= 5:
        return 5, 95  # Wider range for moderate trading
    elif leverage <= 10:
        return 3, 97  # Balanced range for mid-leverage
    else:
        return 2, 98  # Tighter range for aggressive leverage

def analyse(sym, interval="5M", limit=400, leverage=5):
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
    boll_lower, boll_upper = fetch_bollinger(sym, interval)
    if boll_lower and boll_upper:
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
