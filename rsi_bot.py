import os
import time, math, requests, json
from pathlib import Path
import numpy as np

# --- CONFIGURATION ---
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TOP_N = 100
TOP_PICKS = 5       # Adjust this to change how many top signals are sent
VOL_THRESHOLD = 2.5
STATE_FILE = Path("active_grids.json")
ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}

# --- TELEGRAM ALERT ---
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Telegram credentials not set.")
        return
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        response.raise_for_status()
    except Exception as e:
        print("Telegram error:", e)

# --- API CALLS ---
def fetch_symbols():
    try:
        r = requests.get("https://api.pionex.com/api/v1/market/tickers", params={"type": "PERP"}, timeout=10)
        data = r.json()
        tickers = data.get("data", {}).get("tickers", [])
        symbols = sorted([s["symbol"] for s in tickers if "USDT" in s["symbol"]], reverse=True)[:TOP_N]
        return symbols
    except Exception as e:
        print("Error fetching symbols:", e)
        return []

def fetch_closes(sym, interval="60M"):
    try:
        r = requests.get("https://api.pionex.com/api/v1/market/klines",
            params={"symbol": sym, "interval": interval, "limit": 200, "type": "PERP"},
            timeout=10,
        )
        data = r.json()
        klines = data.get("data", {}).get("klines", [])
        return [float(x[4]) for x in klines if isinstance(x, list) and len(x) >= 5]
    except Exception as e:
        print(f"Error fetching klines for {sym}:", e)
        return []

# --- METRICS & ANALYSIS FUNCTIONS ---
def compute_std_dev(closes, period=30):
    if len(closes) < period:
        return 0
    return round(float(np.std(closes[-period:])), 5)

last_trade_time = {}

def compute_cooldown(vol_pct, std_dev):
    base = 300  # seconds
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
    current = closes[-1]
    rng = high - low
    if rng <= 0 or current == 0:
        return None
    pos = (current - low) / rng
    # Only trigger if price is near the edges (outside 25%-75% of range)
    if 0.25 <= pos <= 0.75:
        return None
    std_dev = compute_std_dev(closes)
    vol_pct = rng / current * 100
    v_factor = vol_pct + std_dev * 100
    spacing = max(0.3, min(1.2, 0.75 * (30 / max(v_factor, 1))))
    grids = max(10, min(200, math.floor(rng / (current * spacing / 100))))
    cycle = round((grids * spacing) / (v_factor + 1e-9) * 2, 1)
    if cycle > 2.0:
        return None
    return {
        "symbol": sym,
        "zone": "Long" if pos < 0.25 else "Short",
        "low": low,
        "high": high,
        "now": current,
        "grids": grids,
        "spacing": round(spacing, 2),
        "vol": round(vol_pct, 1),
        "std": std_dev,
        "cycle": cycle
    }

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

# --- SCORING & MESSAGING ---
def score_opportunity(d):
    v = d["vol"]
    c = max(0.1, d["cycle"])
    s = d["spacing"]
    g = min(200, d["grids"])
    return round((v * 2) + (((200 - g) / 200) * 10) + ((1.5 - min(s, 1.5)) * 15) + ((1.5 / c) * 10), 1)

def leverage_hint(spacing):
    if spacing <= 0.5:
        return "20x–50x"
    elif spacing <= 0.75:
        return "10x–25x"
    else:
        return "5x–15x"

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

# --- STATE & ALERTS ---
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    else:
        return {}

def save_state(d):
    STATE_FILE.write_text(json.dumps(d, indent=2))

def stop_msg(sym, reason, info):
    def fmt(p):
        if p < 0.1:
            return f"${p:.8f}"
        elif p < 1:
            return f"${p:,.4f}"
        else:
            return f"${p:,.2f}"
    return (
        f"🛑 Exit Alert: {sym}\n"
        f"📉 Reason: {reason}\n"
        f"📊 Range: {fmt(info['low'])} – {fmt(info['high'])}\n"
        f"💱 Current Price: {fmt(info['now'])}"
    )

# --- MAIN EXECUTION ---
def main():
    prev = load_state()
    nxt = {}
    candidates = []
    stop_alerts = []

    symbols = fetch_symbols()
    print(f"Found {len(symbols)} symbols.")
    for sym in symbols:
        res = scan_with_fallback(sym)
        if res:
            res["score"] = score_opportunity(res)
            nxt[sym] = {"zone": res["zone"], "low": res["low"], "high": res["high"]}
            candidates.append(res)
    
    print(f"Found {len(candidates)} potential candidates.")
    # Sort candidates descending by score
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_signals = candidates[:TOP_PICKS]

    if top_signals:
        msg = f"📊 Grid Signal Scoreboard (Top {TOP_PICKS})\n\n"
        for i, d in enumerate(top_signals, start=1):
            msg += format_ranked_signal(d, i) + "\n\n"
        tg(msg.strip())
        print("Telegram message sent for top signals.")
    else:
        # Send a fallback message so you know the scan did run
        tg("No grid opportunities met criteria.")
        print("No candidate signals found; sent fallback telegram message.")

    # Check for grids that no longer meet criteria.
    for sym in set(prev.keys()) - set(nxt.keys()):
        mid = (prev[sym]["low"] + prev[sym]["high"]) / 2
        stop_alerts.append(stop_msg(sym, "No longer meets criteria", {"low": prev[sym]["low"], "high": prev[sym]["high"], "now": mid}))
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
        print("Sent exit alerts for removed signals.")

if __name__ == "__main__":
    print("🔁 Starting hybrid grid scanner...")
    main()
    print("✅ Scanner finished.")
