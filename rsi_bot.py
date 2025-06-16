"""
Enhanced Grid Scanner v5.1-rev2
• Dynamic grids & spacing
• Cycle ≤ 2 d
• Start + Stop alerts
• Robust kline parsing (dict OR list)
"""

import os, json, math, logging, time, requests, numpy as np
from pathlib import Path
from datetime import datetime

# ── TELEGRAM ─────────────────────────────────────────
TG_TOKEN   = os.getenv("TG_TOKEN")   or os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id":TG_CHAT_ID,"text":msg,"parse_mode":"Markdown"},
            timeout=10).raise_for_status()
    except Exception as e:
        logging.error("Telegram error: %s", e)

# ── CONSTANTS ───────────────────────────────────────
API             = "https://api.pionex.com/api/v1"
TOP_N           = 100
MIN_NOTIONAL_USD= 1_000_000
SPACING_MIN     = 0.3
SPACING_MAX     = 1.2
SPACING_TARGET  = 0.75
CYCLE_MAX       = 2.0
STOP_BUFFER     = 0.01
STATE_FILE      = Path("active_grids.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WRAPPED = {"WBTC","WETH","WSOL","WBNB"}
STABLE  = {"USDT","USDC","BUSD","DAI"}
EXCL    = {"LUNA","LUNC","USTC"}

# ── HELPERS ─────────────────────────────────────────
def valid(sym):
    u=sym.upper()
    return (u.split("_")[0] not in WRAPPED|STABLE|EXCL and
            not u.endswith(("UP","DOWN","3L","3S","5L","5S")))

def fetch_symbols():
    r = requests.get(f"{API}/market/tickers", params={"type":"PERP"}, timeout=10)
    tickers = r.json().get("data", {}).get("tickers", [])
    pairs=[t for t in tickers if valid(t["symbol"])
           and float(t.get("amount",0))>MIN_NOTIONAL_USD]
    pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
    return [p["symbol"] for p in pairs][:TOP_N]

def fetch_closes(sym):
    r = requests.get(f"{API}/market/klines",
                     params={"symbol":sym,"interval":"60M","limit":200,"type":"PERP"},
                     timeout=10)
    payload=r.json().get("data",{})
    kl=payload.get("klines") or payload
    closes=[]
    for k in kl:
        # dict style
        if isinstance(k,dict) and "close" in k:
            closes.append(float(k["close"]))
        # list style index 4
        elif isinstance(k,(list,tuple)) and len(k)>=5:
            closes.append(float(k[4]))
    return closes

def analyse(sym):
    closes=fetch_closes(sym)
    if len(closes)<60: return None
    low, high = min(closes), max(closes)
    rng = high-low
    if rng<=0: return None
    px = closes[-1]
    pos=(px-low)/rng
    if 0.25<=pos<=0.75: return None
    zone="Long" if pos<0.25 else "Short"
    vol_pct=rng/px*100
    spacing=max(SPACING_MIN,min(SPACING_MAX,SPACING_TARGET*(30/max(vol_pct,1))))
    grids=max(10,min(200,math.floor(rng/(px*spacing/100))))
    cycle=round((grids*spacing)/(vol_pct+1e-9)*2,1)
    if cycle>CYCLE_MAX: return None
    return dict(symbol=sym,zone=zone,low=low,high=high,now=px,
                grids=grids,spacing=round(spacing,2),
                vol=round(vol_pct,1),cycle=cycle)

def money(p): return f"${p:.8f}" if p<0.1 else f"${p:,.4f}" if p<1 else f"${p:,.2f}"
ZONE_EMO={"Long":"🟢 Long","Short":"🔴 Short"}
def start_msg(d):
    lev="20x–50x" if d["spacing"]<=0.5 else "10x–25x" if d["spacing"]<=0.75 else "5x–15x"
    return (f"📈 Start Grid Bot: {d['symbol']}\n"
            f"📊 Range: {money(d['low'])} – {money(d['high'])}\n"
            f"📈 Entry Zone: {ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: {d['grids']}  |  📏 Spacing: {d['spacing']}%\n"
            f"🌪️ Volatility: {d['vol']}%  |  ⏱️ Cycle: {d['cycle']} d\n"
            f"⚙️ Leverage Hint: {lev}")

def stop_msg(sym,reason,info):
    return (f"🛑 Exit Alert: {sym}\n"
            f"📉 Reason: {reason}\n"
            f"📊 Range: {money(info['low'])} – {money(info['high'])}\n"
            f"💱 Current Price: {money(info['now'])}")

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

def save_state(d): STATE_FILE.write_text(json.dumps(d,indent=2))

# ── MAIN ───────────────────────────────────────────
def main():
    prev=load_state(); nxt={}; start=[]; stop=[]
    for sym in fetch_symbols():
        res=analyse(sym)
        if not res: continue
        nxt[sym]={"zone":res["zone"],"low":res["low"],"high":res["high"]}
        if sym not in prev:
            start.append(start_msg(res))
        else:
            p=prev[sym]
            if p["zone"]!=res["zone"]:
                stop.append(stop_msg(sym,"Trend flip",res))
            elif res["now"]>p["high"]*(1+STOP_BUFFER) or res["now"]<p["low"]*(1-STOP_BUFFER):
                stop.append(stop_msg(sym,"Price exited range",res))
    for gone in set(prev)-set(nxt):
        mid=(prev[gone]["low"]+prev[gone]["high"])/2
        stop.append(stop_msg(gone,"No longer meets criteria",
                 {"low":prev[gone]["low"],"high":prev[gone]["high"],"now":mid}))
    save_state(nxt)

    for bucket in (start,stop):
        if not bucket: continue
        buf=""
        for m in bucket:
            if len(buf)+len(m)+2>4000: tg(buf); buf=m+"\n\n"
            else: buf+=m+"\n\n"
        if buf: tg(buf)

if __name__=="__main__":
    main()
