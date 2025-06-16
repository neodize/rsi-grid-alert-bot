"""
Enhanced Grid Scanner — Pionex PERP (v5.0)
"""

import os, json, logging, requests, math
from datetime import datetime
from statistics import mean

# ---------- CONFIG ----------
PIONEX = "https://api.pionex.com"
INTVL  = "60M"
LIMIT  = 200
TOP_N  = 10
GRID_TARGET_SPACING = 0.75   # %
GRID_MIN_SPACING    = 0.35   # %
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELE_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = "last_symbols.txt"
HYPE       = "HYPE_USDT_PERP"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# --------- EXCLUDE LISTS ----------
WRAPPED = {"WBTC","WETH","WSOL","WBNB","WFTM","WMATIC","WAVAX"}
STABLE  = {"USDT","USDC","BUSD","DAI"}
EXCLUDED= {"BTCUP","BTCDOWN","ETHUP","ETHDOWN","LUNA","LUNC","USTC"}

def good(sym:str)->bool:
    u=sym.upper()
    if any(u.endswith(s) for s in ("UP","DOWN","3L","3S","5L","5S")): return False
    base=u.split("_")[0]
    return base not in WRAPPED|STABLE|EXCLUDED

# ---------- TELEGRAM ----------
def tg_send(text:str):
    if not (TELE_TOKEN and TELE_CHAT): return
    url=f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
    try:
        requests.post(url,json={"chat_id":TELE_CHAT,"text":text,"parse_mode":"Markdown"},timeout=10)
    except Exception as e:
        logging.error("Telegram error: %s",e)

# ---------- API ----------
def top_perp_symbols()->list[str]:
    url=f"{PIONEX}/api/v1/market/tickers"
    js=requests.get(url,params={"type":"PERP"},timeout=10).json()
    tickers=js.get("data",{}).get("tickers",[])
    sorted_tk=sorted(tickers,key=lambda x: float(x["amount"]),reverse=True)
    symbols=[t["symbol"] for t in sorted_tk if good(t["symbol"])]
    return symbols[:TOP_N]

def fetch_klines(spot:str):
    url=f"{PIONEX}/api/v1/market/klines"
    js=requests.get(url,params={"symbol":spot,"interval":INTVL,"limit":LIMIT,"type":"PERP"},timeout=10).json()
    kl=js.get("data",{}).get("klines") or js.get("data")  # backup structure
    if not kl: raise RuntimeError("no klines")
    closes=[float(k["close"]) if isinstance(k,dict) else float(k[4]) for k in kl]
    highs =[float(k["high"])  if isinstance(k,dict) else float(k[2]) for k in kl]
    lows  =[float(k["low"])   if isinstance(k,dict) else float(k[3]) for k in kl]
    return closes,highs,lows

# ---------- ANALYSE ----------
def analyse(perp:str):
    spot=perp.replace("_PERP","")
    try:
        closes,highs,lows=fetch_klines(spot)
    except Exception as e:
        logging.warning("Skip %s: %s",perp,e); return None
    if len(closes)<100: return None
    lo,hi=min(closes),max(closes)
    band=hi-lo
    now=closes[-1]
    if band<=0: return None
    pos=(now-lo)/band
    if pos<0.05 or pos>0.95: return None
    zone="Long" if pos<0.25 else "Short" if pos>0.75 else "Neutral"
    width_pct=band/now*100
    spacing=max(GRID_MIN_SPACING, GRID_TARGET_SPACING)
    grids=max(2,int(width_pct/spacing))
    cycle_days=round((grids*spacing)/width_pct*2 ,1) if width_pct else "-"
    fmt=lambda p: f"${p:.8f}" if p<0.1 else f"${p:,.4f}" if p<1 else f"${p:,.2f}"
    return dict(symbol=perp,range=f"{fmt(lo)} – {fmt(hi)}",zone=zone,
                grids=grids,spacing=f"{spacing:.2f}%",vol=f"{width_pct:.1f}%",
                cycle=str(cycle_days))

def build_msg(d):
    return (f"*{d['symbol']}*\n"
            f"📊 Range: `{d['range']}`\n"
            f"🎯 Entry Zone: `{d['zone']}`\n"
            f"🧮 Grids: `{d['grids']}`  |  📏 Spacing: `{d['spacing']}`\n"
            f"🌪️ Volatility: `{d['vol']}`  |  ⏱️ Cycle: `{d['cycle']} days`")

# ---------- STATE ----------
def load_last()->set[str]:
    if not os.path.exists(STATE_FILE): return set()
    with open(STATE_FILE) as f: return set(map(str.strip,f))
def save_current(s:set[str]): open(STATE_FILE,"w").write("\n".join(sorted(s)))

# ---------- MAIN ----------
def main():
    symbols=top_perp_symbols()
    if HYPE not in symbols: symbols.append(HYPE)
    now_msgs=[]; current=set()
    for sym in symbols:
        res=analyse(sym)
        if res:
            current.add(sym); now_msgs.append(build_msg(res))
    last=load_last()
    dropped=sorted(list(last-current))
    for d in dropped:
        now_msgs.append(f"🛑 *{d}* dropped – consider closing its grid bot.")
    save_current(current)
    if not now_msgs:
        logging.info("No valid entries.")
        return
    # chunk
    buf=""; chunks=[]
    for m in now_msgs:
        if len(buf)+len(m)+2>4000: chunks.append(buf); buf=m+"\n\n"
        else: buf+=m+"\n\n"
    if buf: chunks.append(buf)
    for c in chunks: tg_send(c)

if __name__=="__main__":
    main()
