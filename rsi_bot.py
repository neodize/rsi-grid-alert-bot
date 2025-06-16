"""
rsi_bot.py  –  Enhanced Grid Scanner v5.1-rev1
✅ Dynamic grids  ✅ Cycle ≤2 d  ✅ Start & Stop alerts  ✅ Pionex-only
"""

import os, json, math, time, requests, logging, numpy as np
from datetime import datetime
from pathlib import Path

# ── ENV / TELEGRAM ───────────────────────────────────────────
TG_TOKEN   = os.getenv("TG_TOKEN")   or os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
def tg(msg: str):
    if not (TG_TOKEN and TG_CHAT_ID): return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        ).raise_for_status()
    except Exception as e:
        logging.error("Telegram error: %s", e)

# ── CONSTANTS ────────────────────────────────────────────────
API = "https://api.pionex.com/api/v1"
TOP_N           = 100
MIN_NOTIONAL    = 1_000_000      # 24 h notional to keep small caps
SPACING_MIN     = 0.3            # % floor
SPACING_MAX     = 1.2            # % cap
SPACING_TARGET  = 0.75           # baseline
CYCLE_MAX       = 2.0            # days
STOP_BUFFER     = 0.01           # 1 % outside range
STATE_FILE      = Path("active_grids.json")

WRAPPED = {"WBTC","WETH","WSOL","WBNB"}
STABLE  = {"USDT","USDC","BUSD","DAI"}
EXCL    = {"LUNA","LUNC","USTC"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── HELPERS ──────────────────────────────────────────────────
def good(sym:str)->bool:
    u=sym.upper()
    return (u.split("_")[0] not in WRAPPED|STABLE|EXCL and
            not u.endswith(("UP","DOWN","3L","3S","5L","5S")))

def fetch_symbols():
    r = requests.get(f"{API}/market/tickers", params={"type":"PERP"}, timeout=10)
    tks = r.json().get("data", {}).get("tickers", [])
    tks = [t for t in tks
           if good(t["symbol"]) and float(t.get("amount",0))>MIN_NOTIONAL]
    tks.sort(key=lambda x: float(x["amount"]), reverse=True)
    return [t["symbol"] for t in tks][:TOP_N]

def fetch_closes(sym):
    r = requests.get(f"{API}/market/klines",
                     params={"symbol":sym,"interval":"60M","limit":200,"type":"PERP"},
                     timeout=10)
    kl = r.json().get("data",{}).get("klines") or []
    return [float(k[4]) for k in kl] if kl else []

# ── ANALYSIS ─────────────────────────────────────────────────
def analyse(sym):
    closes = fetch_closes(sym)
    if len(closes) < 60:
        return None
    lo, hi = min(closes), max(closes)
    rng = hi - lo
    if rng<=0: return None
    px  = closes[-1]
    pos = (px - lo)/rng
    if 0.25<=pos<=0.75: return None             # neutral
    zone = "Long" if pos<0.25 else "Short"

    # volatility & grid maths
    vol_pct = rng/px*100
    spacing = max(SPACING_MIN,
                  min(SPACING_MAX, SPACING_TARGET*(30/max(vol_pct,1))))
    grids   = max(10, min(200, math.floor(rng/(px*spacing/100))))

    # cycle est ~ proportional to grid density vs range
    cycle = round((grids*spacing)/(vol_pct+1e-9)*2,1)
    if cycle> CYCLE_MAX: return None

    return dict(symbol=sym, zone=zone, low=lo, high=hi, now=px,
                grids=grids, spacing=round(spacing,2),
                vol=round(vol_pct,1), cycle=cycle)

# ── STATE I/O ────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(d):
    STATE_FILE.write_text(json.dumps(d, indent=2))

# ── MESSAGE BUILDERS ────────────────────────────────────────
ZONE_EMO = {"Long":"🟢 Long","Short":"🔴 Short"}
def money(p): return f"${p:.8f}" if p<0.1 else f"${p:,.4f}" if p<1 else f"${p:,.2f}"
def build_start(d):
    lev = "20x–50x" if d["spacing"]<=0.5 else "10x–25x" if d["spacing"]<=0.75 else "5x–15x"
    return (f"📈 Start Grid Bot: {d['symbol']}\n"
            f"📊 Range: {money(d['low'])} – {money(d['high'])}\n"
            f"📈 Entry Zone: {ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: {d['grids']}  |  📏 Spacing: {d['spacing']}%\n"
            f"🌪️ Volatility: {d['vol']}%  |  ⏱️ Cycle: {d['cycle']} d\n"
            f"⚙️ Leverage Hint: {lev}")

def build_stop(sym, reason, info):
    return (f"🛑 Exit Alert: {sym}\n"
            f"📉 Reason: {reason}\n"
            f"📊 Range: {money(info['low'])} – {money(info['high'])}\n"
            f"💱 Current Price: {money(info['now'])}")

# ── MAIN RUN ────────────────────────────────────────────────
def main():
    prev = load_state()          # {sym:{zone,low,high}}
    next_state={}
    start_msgs=[]; stop_msgs=[]
    for sym in fetch_symbols():
        res = analyse(sym)
        if not res: continue
        next_state[sym]={"zone":res["zone"],"low":res["low"],"high":res["high"]}
        if sym not in prev:
            start_msgs.append(build_start(res))
        else:
            p = prev[sym]
            if p["zone"]!=res["zone"]:
                stop_msgs.append(build_stop(sym, "Trend flip", res))
            elif res["now"]>p["high"]*(1+STOP_BUFFER) or res["now"]<p["low"]*(1-STOP_BUFFER):
                stop_msgs.append(build_stop(sym,"Price exited range",res))
    # any symbols vanished
    for sym in set(prev)-set(next_state):
        mid=(prev[sym]["low"]+prev[sym]["high"])/2
        dummy=dict(low=prev[sym]["low"],high=prev[sym]["high"],now=mid)
        stop_msgs.append(build_stop(sym,"No longer meets criteria",dummy))
    save_state(next_state)

    # send telegram chunks
    for bucket in (start_msgs, stop_msgs):
        if not bucket: continue
        buf=""
        for m in bucket:
            if len(buf)+len(m)+2>4000:
                tg(buf); buf=m+"\n\n"
            else:
                buf+=m+"\n\n"
        if buf: tg(buf)

if __name__=="__main__":
    main()
