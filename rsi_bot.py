import os
import time, math, requests, json
from pathlib import Path
import numpy as np

# CONFIG
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TOP_N = 100
TOP_PICKS = 5
VOL_THRESHOLD = 2.5
STOP_BUFFER = 0.01
STATE_FILE = Path("active_grids.json")
ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}

# TELEGRAM
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass

# SYMBOLS & CANDLES
def fetch_symbols():
    r = requests.get("https://api.pionex.com/api/v1/market/tickers", params={"type": "PERP"}, timeout=10)
    d = r.json().get("data", {}).get("tickers", [])
    return sorted([s["symbol"] for s in d if "USDT" in s["symbol"]], reverse=True)[:TOP_N]

def fetch_closes(sym, interval="60M"):
    r = requests.get("https://api.pionex.com/api/v1/market/klines",
        params={"symbol": sym, "interval": interval, "limit": 200, "type": "PERP"},
        timeout=10,
    )
    k = r.json().get("data", {}).get("klines", [])
    return [float(x[4]) for x in k if isinstance(x, list) and len(x) >= 5]

# SCAN & ANALYSIS
def compute_std_dev(closes, period=30):
    return round(float(np.std(closes[-period:])), 5) if len(closes) >= period else 0

last_trade_time = {}

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

def analyse(sym, interval="60M"):
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
    spacing = max(0.3, min(1.2, 0.75 * (30 / max(v_factor, 1))))
    grids = max(10, min(200, math.floor(rng / (px * spacing / 100))))
    cycle = round((grids * spacing) / (v_factor + 1e-9) * 2, 1)
    if cycle > 2.0:
        return None
    return dict(symbol=sym, zone="Long" if pos < 0.25 else "Short",
                low=low, high=high, now=px,
                grids=grids, spacing=round(spacing, 2),
                vol=round(vol_pct, 1), std=std_dev, cycle=cycle)

def scan_with_fallback(sym):
    res_60m = analyse(sym, "60M")
    if not res_60m:
        return None
    if res_60m["vol"] >= VOL_THRESHOLD:
        res_5m = analyse(sym, "5M")
        if res_5m and should_trigger(sym, res_5m["vol"], res_5m["std"]):
            return res_5m
    elif should_trigger(sym, res_60m["vol"], res_60m["std"]):
        return res_60m
    return None

# SCORING & RANKING
def score_opportunity(d):
    v = d["vol"]
    c = max(0.1, d["cycle"])
    s = d["spacing"]
    g = min(200, d["grids"])
    return round((v * 2) + ((200 - g)/200)*10 + ((1.5 - min(s, 1.5))*15) + (1.5/c)*10, 1)

def leverage_hint(spacing):
    return "20x–50x" if spacing <= 0.5 else "10x–25x" if spacing <= 0.75 else "5x–15x"

def format_ranked_signal(d, rank):
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    tag = medals[rank-1] if rank <= 5 else f"{rank}️⃣"
    return (
        f"{tag} {d['symbol']}\n"
        f"📈 Entry Zone: {ZONE_EMO[d['zone']]}\n"
        f"🌪️ Volatility: {d['vol']}%\n"
        f"📏 Spacing: {d['spacing']}%\n"
        f"🧮 Grids: {d['grids']}\n"
        f"⏱️ Cycle: {d['cycle']} d\n"
        f"🌀 Score: {d['score']}\n"
        f"⚙️ Leverage Hint: {leverage_hint(d['spacing'])}"
    )

# STATE
def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

def save_state(d):
    STATE_FILE.write_text(json.dumps(d, indent=2))

def stop_msg(sym, reason, info):
    def fmt(p): return f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"
    return (
        f"🛑 Exit Alert: {sym}\n"
        f"📉 Reason: {reason}\n"
        f"📊 Range: {fmt(info['low'])} – {fmt(info['high'])}\n"
        f"💱 Current Price: {fmt(info['now'])}"
    )

# MAIN
def main():
    prev = load_state()
    nxt, candidates, stop_alerts = {}, [], []

    for sym in fetch_symbols():
        res = scan_with_fallback(sym)
        if not res:
            continue
        res["score"] = score_opportunity(res)
        nxt[sym] = {"zone": res["zone"], "low": res["low"], "high": res["high"]}
        candidates.append(res)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_signals = candidates[:TOP_PICKS]

    if top_signals:
        msg = f"📊 Grid Signal Scoreboard (Top {TOP_PICKS})\n\n"
        for i, d in enumerate(top_signals, 1):
            msg += format_ranked_signal(d, i) + "\n\n"
        tg(msg.strip())

    for gone in set(prev) - set(nxt):
        mid = (prev[gone]["low"] + prev[gone]["high"]) / 2
        stop_alerts.append(stop_msg(gone, "No longer meets criteria",
            {"low": prev[gone]["low"], "high": prev[gone]["high"], "now": mid}))
    save_state(nxt)

    if stop_alerts:
        buf = ""
        for m in stop_alerts:
            if len(buf) + len(m) + 2 > 4000:
                tg(buf)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            tg(buf)

if __name__ == "__main__":
    main()
