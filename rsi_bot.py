import os, json, math, logging, time, requests
from pathlib import Path
import numpy as np

# ── TELEGRAM CONFIG ──────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

def tg(msg):
    print(f"DEBUG: Attempting to send Telegram message: {msg[:100]}...")
    if not TG_TOKEN:
        print("DEBUG: TG_TOKEN is empty or not set")
        return
    if not TG_CHAT_ID:
        print("DEBUG: TG_CHAT_ID is empty or not set")
        return
    
    print(f"DEBUG: TG_TOKEN exists: {bool(TG_TOKEN)} (length: {len(TG_TOKEN)})")
    print(f"DEBUG: TG_CHAT_ID exists: {bool(TG_CHAT_ID)} (value: {TG_CHAT_ID})")
    
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        
        print(f"DEBUG: Sending POST to: {url[:50]}...")
        response = requests.post(url, json=payload, timeout=10)
        
        print(f"DEBUG: Response status: {response.status_code}")
        print(f"DEBUG: Response text: {response.text}")
        
        response.raise_for_status()
        print("DEBUG: Telegram message sent successfully!")
        
    except Exception as e:
        print(f"DEBUG: Telegram error: {e}")
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

# ── HELPER FUNCTIONS ───────────────────────────────
def valid(sym):
    u = sym.upper()
    return (u.split("_")[0] not in WRAPPED | STABLE | EXCL and 
            not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def fetch_symbols():
    print("DEBUG: Fetching symbols...")
    try:
        r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
        print(f"DEBUG: API response status: {r.status_code}")
        
        tickers = r.json().get("data", {}).get("tickers", [])
        print(f"DEBUG: Got {len(tickers)} tickers")
        
        pairs = [t for t in tickers if valid(t["symbol"]) and float(t.get("amount", 0)) > MIN_NOTIONAL_USD]
        print(f"DEBUG: {len(pairs)} pairs meet criteria")
        
        pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
        result = [p["symbol"] for p in pairs][:TOP_N]
        print(f"DEBUG: Top {len(result)} symbols: {result[:5]}...")
        return result
    except Exception as e:
        print(f"DEBUG: Error fetching symbols: {e}")
        return []

def fetch_closes(sym, interval="5M"):
    try:
        r = requests.get(f"{API}/market/klines", 
                        params={"symbol": sym, "interval": interval, "limit": 200, "type": "PERP"}, 
                        timeout=10)
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
        print(f"DEBUG: Error fetching closes for {sym}: {e}")
        return []

def compute_std_dev(closes, period=30):
    return float(np.std(closes[-period:])) if len(closes) >= period else 0

def compute_cooldown(vol_pct, std_dev):
    base = 300  # 5 minutes in seconds
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

ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}

def start_msg(d):
    lev = "20x–50x" if d["spacing"] <= 0.5 else "10x–25x" if d["spacing"] <= 0.75 else "5x–15x"
    return (f"📈 Start Grid Bot: {d['symbol']}\n"
            f"📊 Range: {money(d['low'])} – {money(d['high'])}\n"
            f"📈 Entry Zone: {ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: {d['grids']} | 📏 Spacing: {d['spacing']}%\n"
            f"🌪️ Volatility: {d['vol']}% | ⏱️ Cycle: {d['cycle']} d\n"
            f"⚙️ Leverage Hint: {lev}")

def stop_msg(sym, reason, info):
    return (f"🛑 Exit Alert: {sym}\n"
            f"📉 Reason: {reason}\n"
            f"📊 Range: {money(info['low'])} – {money(info['high'])}\n"
            f"💱 Current Price: {money(info['now'])}")

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

def save_state(d):
    STATE_FILE.write_text(json.dumps(d, indent=2))

def analyse(sym, interval="5M"):
    print(f"DEBUG: Analyzing {sym} with {interval} interval")
    closes = fetch_closes(sym, interval)
    if len(closes) < 60:
        print(f"DEBUG: Not enough data for {sym} ({len(closes)} closes)")
        return None
    
    low, high = min(closes), max(closes)
    px = closes[-1]
    rng = high - low
    
    if rng <= 0 or px == 0:
        print(f"DEBUG: Invalid range/price for {sym}")
        return None
    
    pos = (px - low) / rng
    if 0.25 <= pos <= 0.75:
        print(f"DEBUG: {sym} position {pos:.2f} not in entry zone")
        return None
    
    std_dev = compute_std_dev(closes)
    vol_pct = rng / px * 100
    v_factor = vol_pct + std_dev * 100
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(v_factor, 1))))
    grids = max(10, min(200, math.floor(rng / (px * spacing / 100))))
    cycle = round((grids * spacing) / (v_factor + 1e-9) * 2, 1)
    
    if cycle > CYCLE_MAX:
        print(f"DEBUG: {sym} cycle {cycle} too long")
        return None
    
    result = dict(symbol=sym, zone="Long" if pos < 0.25 else "Short", 
                 low=low, high=high, now=px, grids=grids, 
                 spacing=round(spacing, 2), vol=round(vol_pct, 1), 
                 std=round(std_dev, 5), cycle=cycle)
    
    print(f"DEBUG: {sym} analysis successful: vol={vol_pct:.1f}%, pos={pos:.2f}")
    return result

# ── HYBRID SCANNING ─────────────────────────────────
def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    # Broad/efficient scan with 60M interval
    res_60m = analyse(sym, interval="60M")
    if not res_60m:
        return None
    
    # If volatility is high, refine the data by scanning using 5M interval
    if res_60m['vol'] >= vol_threshold:
        res_5m = analyse(sym, interval="5M")
        if res_5m and should_trigger(sym, res_5m["vol"], res_5m["std"]):
            return res_5m
        else:
            return None
    else:
        # Use the 60M result if it passes cooldown
        if should_trigger(sym, res_60m["vol"], res_60m["std"]):
            return res_60m
        else:
            return None

# ── MAIN ───────────────────────────────────────────
def main():
    print("DEBUG: Starting main function...")
    
    # Test Telegram first
    print("DEBUG: Testing Telegram connection...")
    tg("🔧 Bot starting up - testing connection...")
    
    prev = load_state()
    print(f"DEBUG: Loaded previous state with {len(prev)} symbols")
    
    nxt, start_alerts, stop_alerts = {}, [], []
    
    symbols = fetch_symbols()
    print(f"DEBUG: Will scan {len(symbols)} symbols")
    
    processed = 0
    for sym in symbols:
        processed += 1
        if processed % 10 == 0:
            print(f"DEBUG: Processed {processed}/{len(symbols)} symbols")
            
        res = scan_with_fallback(sym)
        if not res:
            continue
        
        print(f"DEBUG: {sym} passed analysis")
        nxt[sym] = {"zone": res["zone"], "low": res["low"], "high": res["high"]}
        
        if sym not in prev:
            print(f"DEBUG: New signal for {sym}")
            start_alerts.append(start_msg(res))
        else:
            p = prev[sym]
            if p["zone"] != res["zone"]:
                print(f"DEBUG: Zone change for {sym}")
                stop_alerts.append(stop_msg(sym, "Trend flip", res))
            elif res["now"] > p["high"] * (1 + STOP_BUFFER) or res["now"] < p["low"] * (1 - STOP_BUFFER):
                print(f"DEBUG: Price exit for {sym}")
                stop_alerts.append(stop_msg(sym, "Price exited range", res))
    
    # Handle removed symbols
    for gone in set(prev) - set(nxt):
        print(f"DEBUG: Symbol {gone} no longer meets criteria")
        mid = (prev[gone]["low"] + prev[gone]["high"]) / 2
        stop_alerts.append(stop_msg(gone, "No longer meets criteria", 
                                  {"low": prev[gone]["low"], "high": prev[gone]["high"], "now": mid}))
    
    save_state(nxt)
    print(f"DEBUG: Saved new state with {len(nxt)} symbols")
    print(f"DEBUG: Generated {len(start_alerts)} start alerts, {len(stop_alerts)} stop alerts")
    
    # Send alerts in batches
    for alert_type, alerts in [("start", start_alerts), ("stop", stop_alerts)]:
        if not alerts:
            print(f"DEBUG: No {alert_type} alerts to send")
            continue
            
        print(f"DEBUG: Sending {len(alerts)} {alert_type} alerts")
        buf = ""
        for msg in alerts:
            if len(buf) + len(msg) + 2 > 4000:
                tg(buf)
                buf = msg + "\n\n"
            else:
                buf += msg + "\n\n"
        if buf:
            tg(buf)
    
    print("DEBUG: Main function completed")

if __name__ == "__main__":
    main()
