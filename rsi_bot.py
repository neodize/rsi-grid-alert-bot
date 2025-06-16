"""
Enhanced Grid Scanner — Pionex PERP v5.0.2
"""

import os, logging, requests

PIONEX   = "https://api.pionex.com"
INTERVAL = "60M"
LIMIT    = 200
TOP_N    = 10

GRID_TARGET_SPACING = 0.75   # %
GRID_MIN_SPACING    = 0.35   # %
FEE_PCT             = 0.10   # 0.05% open + 0.05% close ≈ 0.10%

TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELE_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = "last_symbols.txt"
HYPE       = "HYPE_USDT_PERP"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WRAPPED = {"WBTC","WETH","WSOL","WBNB"}
STABLE  = {"USDT","USDC","BUSD","DAI"}
EXCL    = {"LUNA","LUNC","USTC"}

def good(sym:str)->bool:
    u=sym.upper()
    return (u.split("_")[0] not in WRAPPED|STABLE|EXCL
            and not u.endswith(("UP","DOWN","3L","3S","5L","5S")))

def tg(txt:str):
    if not (TELE_TOKEN and TELE_CHAT): return
    try:
        requests.post(f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage",
                      json={"chat_id":TELE_CHAT,"text":txt,"parse_mode":"Markdown"},timeout=10)
    except Exception as e:
        logging.error("Telegram error: %s",e)

def top_perp_symbols():
    js=requests.get(f"{PIONEX}/api/v1/market/tickers",
                    params={"type":"PERP"},timeout=10).json()
    tk=js.get("data",{}).get("tickers",[])
    tk.sort(key=lambda x: float(x["amount"]),reverse=True)
    return [t["symbol"] for t in tk if good(t["symbol"])][:TOP_N]

def klines(spot:str):
    js=requests.get(f"{PIONEX}/api/v1/market/klines",
                    params={"symbol":spot,"interval":INTERVAL,"limit":LIMIT,"type":"PERP"},
                    timeout=10).json()
    kl=js.get("data",{}).get("klines") or js.get("data")
    if not kl: raise RuntimeError("no klines")
    closes=[float(k["close"]) if isinstance(k,dict) else float(k[4]) for k in kl]
    return closes

ZONE_EMO={"Long":"📈 Entry Zone: 🟢 Long",
          "Neutral":"🔁 Entry Zone: ⚪️ Neutral",
          "Short":"📉 Entry Zone: 🔴 Short"}

def analyse(perp:str):
    spot=perp.replace("_PERP","")
    closes=klines(spot)
    lo,hi=min(closes),max(closes); band=hi-lo; now=closes[-1]
    if band<=0: return None
    pos=(now-lo)/band
    if pos<0.05 or pos>0.95: return None
    zone="Long" if pos<0.25 else "Short" if pos>0.75 else "Neutral"
    width_pct=band/now*100
    spacing=max(GRID_MIN_SPACING, GRID_TARGET_SPACING)
    grids=max(2, int(width_pct/spacing))
    cycle=round((grids*spacing)/width_pct*2,1) if width_pct else "-"
    min_profit=max(0, spacing-FEE_PCT)
    fmt=lambda p: f\"${p:.8f}\" if p<0.1 else f\"${p:,.4f}\" if p<1 else f\"${p:,.2f}\"
    return dict(symbol=perp,range=f\"{fmt(lo)} – {fmt(hi)}\",zone=zone,
                grids=grids,spacing=f\"{spacing:.2f}%\",vol=f\"{width_pct:.1f}%\",
                cycle=f\"{cycle} days\",profit=f\"{min_profit:.2f}%\")

def build(d):
    return (f\"*{d['symbol']}*\\n\"
            f\"📊 Range: `{d['range']}`\\n\"
            f\"{ZONE_EMO[d['zone']]}\\n\"
            f\"🧮 Grids: `{d['grids']}`  |  📏 Spacing: `{d['spacing']}`\\n\"
            f\"💰 Min Profit (after fees): `{d['profit']}`\\n\"
            f\"🌪️ Volatility: `{d['vol']}`  |  ⏱️ Cycle: `{d['cycle']}`\")

def load_last():
    if not os.path.exists(STATE_FILE): return set()
    return set(open(STATE_FILE).read().splitlines())
def save_current(s): open(STATE_FILE,\"w\").write(\"\\n\".join(sorted(s)))

def main():
    try: syms=top_perp_symbols()
    except Exception as e: logging.error(\"Ticker fetch fail: %s\",e); return
    if HYPE not in syms: syms.append(HYPE)

    msgs,current=[],set()
    for s in syms:
        try:
            info=analyse(s)
            if info: current.add(s); msgs.append(build(info))
            elif s==HYPE: msgs.append(\"🟨 *HYPE_USDT*\\nNo recommendation for now.\")
        except Exception as e:
            logging.warning(\"Skip %s: %s\",s,e)

    for d in load_last()-current:
        msgs.append(f\"🛑 *{d}* dropped – consider closing its grid bot.\")

    save_current(current)
    if not msgs: logging.info(\"No valid entries.\"); return
    buf=\"\";chunks=[]
    for m in msgs:
        if len(buf)+len(m)+2>4000: chunks.append(buf); buf=m+\"\\n\\n\"
        else: buf+=m+\"\\n\\n\"
    if buf: chunks.append(buf)
    for c in chunks: tg(c)

if __name__==\"__main__\": main()
