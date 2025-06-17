import os
import json
import math
import logging
import time
import requests
from pathlib import Path
import numpy as np
import argparse

# ── TELEGRAM CONFIG ──────────────────────────────
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", os.environ.get("TG_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", os.environ.get("TG_CHAT_ID", "")).strip()

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

# ── PARAMETERS ────────────────────────────────────
API = "https://api.pionex.com/api/v1"
TOP_N = 100
MIN_NOTIONAL_USD = 1_000_000
SPACING_MIN = 0.3
SPACING_MAX = 1.2
SPACING_TARGET = 0.75
CYCLE_MAX = 2.0
STOP_BUFFER = 0.01
STATE_FILE = Path("active_grids.json")
WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE = {"USDT", "USDC", "BUSD", "DAI"}
EXCL = {"LUNA", "LUNC", "USTC"}
VOL_THRESHOLD = 2.5

last_trade_time = {}
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}
# ── SCORING FUNCTION ─────────────────────────────
def score_signal(d):
    return round(
        d["vol"] * 2 +
        ((200 - d["grids"]) / 200) * 10 +
        ((1.5 - min(d["spacing"], 1.5)) * 15) +
        (1.5 / max(d["cycle"], 0.1)) * 10,
        1
    )

def interpret_score(score):
    # Provide emoji-based suitability interpretation
    if score > 90:
        return "🚀 Ideal for high-volatility scalping"
    elif score > 80:
        return "🎯 Balanced for trend-following bots"
    else:
        return "📊 Moderate conditions — review before deploying"
# ── MARKET UTILITIES ─────────────────────────────
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

def fetch_closes(sym, interval="5M"):
    r = requests.get(f"{API}/market/klines",
                     params={"symbol": sym, "interval": interval, "limit": 200, "type": "PERP"},
                     timeout=10)
    payload = r.json().get("data", {})
    kl = payload.get("klines") or payload
    closes = [float(k["close"]) if isinstance(k, dict) else float(k[4])
              for k in kl if (isinstance(k, dict) and "close" in k) or (isinstance(k, (list, tuple)) and len(k) >= 5)]
    return closes
from datetime import datetime
import pytz

# ── TIME-BASED ALERT HANDLING ─────────────────────
def is_awake_hours():
    # Malaysia: Awake from 9:00 until 01:00; sleeping from 01:00 to 09:00
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    # Return False if between 1h and 9h; True otherwise.
    return not (1 <= now.hour < 9)

def safe_tg_send(msg, urgent_flag=False):
    # Send alert if urgent or if within awake hours.
    if urgent_flag or is_awake_hours():
        tg(msg)
    else:
        logging.info("Non-urgent alert suppressed during sleep hours.")

# ── ANALYSIS FUNCTIONS ───────────────────────────
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

def money(p):
    return f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"

def start_msg(d, rank=None):
    score = score_signal(d)
    lev = "20x–50x" if d["spacing"] <= 0.5 else "10x–25x" if d["spacing"] <= 0.75 else "5x–15x"
    prefix = f"🥇 Top {rank} — {d['symbol']}" if rank else f"📈 Start Grid Bot: {d['symbol']}"
    return (f"{prefix}\n"
            f"📊 Range: {money(d['low'])} – {money(d['high'])}\n"
            f"📈 Entry Zone: {ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: {d['grids']}  |  📏 Spacing: {d['spacing']}%\n"
            f"🌪️ Volatility: {d['vol']}%  |  ⏱️ Cycle: {d['cycle']} d\n"
            f"🌀 Score: {score}\n"
            f"⚙️ Leverage Hint: {lev}\n"
            f"🧭 Suitability: {interpret_score(score)}")

def stop_msg(sym, reason, info):
    return (f"🛑 Exit Alert: {sym}\n"
            f"📉 Reason: {reason}\n"
            f"📊 Range: {money(info['low'])} – {money(info['high'])}\n"
            f"💱 Current Price: {money(info['now'])}")

def analyse(sym, interval="5M"):
    closes = fetch_closes(sym, interval)
    if len(closes) < 60:
        return None
    low, high = min(closes), max(closes)
    px = closes[-1]
    rng = high - low
    if rng <= 0 or px == 0:
        return None
    pos = (px - low) / rng
    if 0.25 <= pos <= 0.75:
        return None
    std_dev = compute_std_dev(closes)
    vol_pct = rng / px * 100
    v_factor = vol_pct + std_dev * 100
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(v_factor, 1))))
    grids = max(10, min(200, math.floor(rng / (px * spacing / 100))))
    cycle = round((grids * spacing) / (v_factor + 1e-9) * 2, 1)
    if cycle > CYCLE_MAX:
        return None
    return dict(symbol=sym, zone="Long" if pos < 0.25 else "Short",
                low=low, high=high, now=px,
                grids=grids, spacing=round(spacing, 2),
                vol=round(vol_pct, 1), std=round(std_dev, 5), cycle=cycle)

def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    r60 = analyse(sym, interval="60M")
    if not r60:
        return None
    if r60['vol'] >= vol_threshold:
        r5 = analyse(sym, interval="5M")
        if r5 and should_trigger(sym, r5["vol"], r5["std"]):
            return r5
        else:
            return None
    else:
        if should_trigger(sym, r60["vol"], r60["std"]):
            return r60
        else:
            return None
# ── STATE MANAGEMENT & MAIN LOGIC ────────────────
def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

def save_state(d):
    STATE_FILE.write_text(json.dumps(d, indent=2))

def main(urgent_mode=False):
    prev = load_state()
    nxt, ranked, stops = {}, [], []

    for sym in fetch_symbols():
        res = scan_with_fallback(sym)
        if not res:
            continue
        nxt[sym] = {"zone": res["zone"], "low": res["low"], "high": res["high"]}
        if sym not in prev:
            ranked.append((score_signal(res), res))
        else:
            p = prev[sym]
            if p["zone"] != res["zone"]:
                stops.append(stop_msg(sym, "Trend flip", res))
            elif res["now"] > p["high"] * (1 + STOP_BUFFER) or res["now"] < p["low"] * (1 - STOP_BUFFER):
                stops.append(stop_msg(sym, "Price exited range", res))

    for gone in set(prev) - set(nxt):
        mid = (prev[gone]["low"] + prev[gone]["high"]) / 2
        stops.append(stop_msg(gone, "No longer meets criteria",
                               {"low": prev[gone]["low"], "high": prev[gone]["high"], "now": mid}))
    save_state(nxt)

    # Send ranked start alerts sorted by score (descending)
    if ranked:
        ranked.sort(reverse=True)
        buf = ""
        for i, (_, signal) in enumerate(ranked, 1):
            m = start_msg(signal, rank=i)
            if len(buf) + len(m) > 3500:
                safe_tg_send(buf, urgent_flag=urgent_mode)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            safe_tg_send(buf, urgent_flag=urgent_mode)

    # Send stop alerts in batches
    if stops:
        buf = ""
        for m in stops:
            if len(buf) + len(m) > 3500:
                safe_tg_send(buf, urgent_flag=urgent_mode)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            safe_tg_send(buf, urgent_flag=urgent_mode)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grid Trading Scanner")
    parser.add_argument("--urgent-only", action="store_true", help="Run urgent scan (alerts bypass sleep hours)")
    args = parser.parse_args()
    # Also check the env variable (for GitHub Actions)
    urgent_mode = args.urgent_only or (os.environ.get("URGENT_SCAN", "").lower() == "true")
    main(urgent_mode=urgent_mode)
