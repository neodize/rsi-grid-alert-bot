"""
Enhanced Grid Scanner – PERP‑only (v4.3)
========================================
Finds top‑volume PERP coins that are safe to start Pionex futures
grid bots, shows Long/Neutral/Short entry zones, grid suggestions,
and issues 🛑 stop alerts when a coin drops out.
"""

import os
import logging
from datetime import datetime
import requests

# ─────────────────── CONFIG ───────────────────
PIONEX_API = "https://api.pionex.com"
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELE_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
INTERVAL   = "60M"
LAST_FILE  = "last_symbols.txt"

GRID_MIN_SPACING = 0.35           # min spacing % to beat fees
TARGET_SPACING   = 0.4            # base spacing %
MAX_MSG_CHARS    = 4000           # telegram safety
HYPE             = "HYPE_USDT_PERP"

# tokens we always skip
WRAPPED = {"WBTC","WETH","WSOL","WBNB","WMATIC","WAVAX","WFTM","CBBTC","CBETH",
           "RETH","STETH","WSTETH","FRXETH","SFRXETH"}
STABLE  = {"USDT","USDC","BUSD","DAI","TUSD","USDP","USDD","FRAX",
           "FDUSD","PYUSD","USDE","USDB","LUSD","SUSD","DUSD","OUSD"}
EXCL    = {"BTCUP","BTCDOWN","ETHUP","ETHDOWN","ADAUP","ADADOWN",
           "LUNA","LUNC","USTC","SHIB","DOGE","PEPE","FLOKI","BABYDOGE"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ───────────────── TELEGRAM ────────────────────
def tg_send(msgs):
    if not TELE_TOKEN or not TELE_CHAT:
        return
    for m in msgs:
        try:
            requests.post(f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage",
                          data={"chat_id":TELE_CHAT, "text":m, "parse_mode":"Markdown"}, timeout=10).raise_for_status()
        except Exception as e:
            logging.error("Telegram error: %s", e)

# ───────────────── API HELPERS ────────────────
def fetch_tickers():
    js = requests.get(f"{PIONEX_API}/api/v1/market/tickers",
                      params={"type":"PERP"}, timeout=10).json()
    return js["data"]["tickers"]

def fetch_klines(symbol):
    """symbol must be ETH_USDT etc. Use type=PERP to get contract data."""
    url = f"{PIONEX_API}/api/v1/market/klines"
    js = requests.get(url,
                      params={"symbol":symbol,"interval":INTERVAL,"limit":200,"type":"PERP"},
                      timeout=10).json()
    if not js.get("data") or "klines" not in js["data"]:
        raise RuntimeError("No klines")
    closes = [float(k["close"]) for k in js["data"]["klines"]]
    highs  = [float(k["high"])  for k in js["data"]["klines"]]
    lows   = [float(k["low"])   for k in js["data"]["klines"]]
    return closes, highs, lows

def perp_to_spot(perp):
    """BTC_USDT_PERP -> BTC_USDT"""
    return perp.replace("_PERP","")

def worth(skipsym):
    s=skipsym.upper()
    return not (s in WRAPPED or s in STABLE or s in EXCL or
                s.endswith(("UP","DOWN","3L","3S","5L","5S")))

# ───────────────── ANALYSIS ───────────────────
def analyse(pair):
    spot = perp_to_spot(pair)
    closes, highs, lows = fetch_klines(spot)
    if len(closes)<100:
        return None
    hi, lo = max(closes), min(closes)
    band = hi-lo
    if band<=0: return None
    now = closes[-1]
    pos = (now-lo)/band          # 0 bottom …1 top
    if pos<0.05 or pos>0.95:     # extreme – skip
        return None

    # Entry zone classification
    if pos<0.25:
        zone = "Long"
    elif pos>0.75:
        zone = "Short"
    else:
        zone = "Neutral"

    width_pct = band/now*100
    spacing   = max(GRID_MIN_SPACING, TARGET_SPACING)
    grids     = max(2,int(width_pct/spacing))

    def fmt(p):
        if p>=1:  return f"${p:,.2f}"
        if p>=0.1:return f"${p:,.4f}"
        return f"${p:.8f}"

    return {
        "symbol": pair,
        "zone":   zone,
        "range":  f"{fmt(lo)} – {fmt(hi)}",
        "grids":  grids,
        "spacing": f"{spacing:.2f}%",
        "vol":    f"{width_pct:.1f}%"
    }

def build_msg(d):
    return (f"*{d['symbol']}*\n"
            f"💡 Entry Zone: {d['zone']}\n"
            f"Price Range: {d['range']}\n"
            f"Grid Count: {d['grids']}  |  Spacing: {d['spacing']}\n"
            f"Volatility: {d['vol']}")

# ───────────────── MAIN ───────────────────────
def main():
    tick = fetch_tickers()
    top = [t for t in sorted(tick, key=lambda x: float(x["amount"]), reverse=True)
           if worth(t["symbol"])][:10]
    if HYPE not in [t["symbol"] for t in top]:
        top.append({"symbol":HYPE})

    msgs, current = [], []
    for t in top:
        res = analyse(t["symbol"])
        if res:
            current.append(res["symbol"])
            msgs.append(build_msg(res))

    # ----- stop suggestions -----
    last=set()
    if os.path.exists(LAST_FILE):
        with open(LAST_FILE) as f:
            last=set(l.strip() for l in f if l.strip())
    dropped=[s for s in last if s not in current]
    for d in dropped:
        msgs.append(f"🛑 *{d}* dropped out – consider stopping its grid bot.")

    # save
    with open(LAST_FILE,"w") as f:
        f.write("\n".join(current))

    if not msgs:
        logging.info("No valid entries.")
        return

    # split to chunks
    buf,ch="",""
    chunks=[]
    for m in msgs:
        if len(buf)+len(m)+2>MAX_MSG_CHARS:
            chunks.append(buf); buf=m+"\n\n"
        else:
            buf+=m+"\n\n"
    if buf: chunks.append(buf)
    tg_send(chunks)

if __name__=="__main__":
    main()
