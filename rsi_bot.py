"""
Enhanced Grid Scanner – Auto‑Discover PERPs v5.0
"""

import os, logging, requests, math

# ── Bot Config ─────────────────────────────────
PIONEX = "https://api.pionex.com"
INTERVAL = "60M"
FULL_LIMIT = 200           # used in final analysis
QUICK_LIMIT = 50           # cheap fetch for candidate scoring
TOP_CANDIDATES = 30        # analyse this many highest‑score symbols

GRID_TARGET_SPACING = 0.75
GRID_MIN_SPACING = 0.35

TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELE_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = "last_symbols.txt"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

WRAPPED = {"WBTC","WETH","WSOL","WBNB"}
STABLE  = {"USDT","USDC","BUSD","DAI"}
EXCL    = {"LUNA","LUNC","USTC"}

PRICE_MIN = 0.005          # skip sub‑penny tokens
VOL_MIN   = 100_000        # $100k 24h notional

# ── Helpers ────────────────────────────────────
def good(sym:str)->bool:
    u=sym.upper()
    return (u.split("_")[0] not in WRAPPED|STABLE|EXCL
            and not u.endswith(("UP","DOWN","3L","3S","5L","5S")))

def tg(msg:str):
    if not (TELE_TOKEN and TELE_CHAT): return
    try:
        requests.post(f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage",
                      json={"chat_id":TELE_CHAT,"text":msg,"parse_mode":"Markdown"},
                      timeout=10)
    except Exception as e:
        logging.error("Telegram error: %s",e)

def fetch_klines(symbol:str, limit:int):
    js=requests.get(f"{PIONEX}/api/v1/market/klines",
                    params={"symbol":symbol,
                            "interval":INTERVAL,
                            "limit":limit,
                            "type":"PERP"},
                    timeout=10).json()
    kl=js.get("data",{}).get("klines") or js.get("data")
    if not kl: raise RuntimeError("no klines")
    highs=[float(k["high"]) if isinstance(k,dict) else float(k[2]) for k in kl]
    lows =[float(k["low"])  if isinstance(k,dict) else float(k[3]) for k in kl]
    closes=[float(k["close"])if isinstance(k,dict) else float(k[4]) for k in kl]
    return closes, highs, lows

# ── 1. Discover candidates quickly ─────────────
def discover_candidates():
    tk=requests.get(f"{PIONEX}/api/v1/market/tickers",
                    params={"type":"PERP"},timeout=10).json()["data"]["tickers"]
    scored=[]
    for t in tk:
        sym=t["symbol"]; price=float(t["close"]); vol=float(t["amount"])
        if price<PRICE_MIN or vol<VOL_MIN or not good(sym): continue
        try:
            _, highs, lows = fetch_klines(sym, QUICK_LIMIT)
            width_pct=(max(highs)-min(lows))/price*100
            score = width_pct / max(1, math.log10(vol))
            scored.append((score, sym))
        except Exception:
            continue
    scored.sort(reverse=True)
    return [s for _, s in scored[:TOP_CANDIDATES]]

# ── 2. Full analysis & message build ───────────
ZONE_EMO={"Long":"📈 Entry Zone: 🟢 Long",
          "Neutral":"🔁 Entry Zone: ⚪️ Neutral",
          "Short":"📉 Entry Zone: 🔴 Short"}

def analyse(sym:str):
    closes, highs, lows = fetch_klines(sym, FULL_LIMIT)
    lo, hi = min(lows), max(highs); band=hi-lo; now=closes[-1]
    if band<=0: return None
    pos=(now-lo)/band
    if pos<0.05 or pos>0.95: return None
    zone = "Long" if pos<0.25 else "Short" if pos>0.75 else "Neutral"
    width_pct=band/now*100
    spacing=max(GRID_MIN_SPACING, GRID_TARGET_SPACING)
    grids=max(2, int(width_pct/spacing))
    cycle=round((grids*spacing)/width_pct*2,1) if width_pct else "-"
    fmt=lambda p: f"${p:.8f}" if p<0.1 else f"${p:,.4f}" if p<1 else f"${p:,.2f}"
    return dict(symbol=sym,range=f"{fmt(lo)} – {fmt(hi)}",zone=zone,
                grids=grids,spacing=f"{spacing:.2f}%",
                vol=f"{width_pct:.1f}%",cycle=f"{cycle} days")

def build(d):
    return (f"*{d['symbol']}*\n"
            f"📊 Range: `{d['range']}`\n"
            f"{ZONE_EMO[d['zone']]}\n"
            f"💰 Grids: `{d['grids']}`  |  📏 Spacing: `{d['spacing']}`\n"
            f"🌪️ Volatility: `{d['vol']}`  |  ⏱️ Cycle: `{d['cycle']}`")

# ── 3. State helpers ───────────────────────────
def load_last():
    if not os.path.exists(STATE_FILE): return set()
    return set(open(STATE_FILE).read().splitlines())
def save_current(s): open(STATE_FILE,"w").write("\n".join(sorted(s)))

# ── 4. Main flow ───────────────────────────────
def main():
    symbols = discover_candidates()
    current, msgs = set(), []
    for sym in symbols:
        try:
            info=analyse(sym)
            if info:
                current.add(sym)
                msgs.append(build(info))
        except Exception as e:
            logging.warning("Skip %s: %s", sym, e)

    dropped = load_last() - current
    for d in dropped:
        msgs.append(f"🛑 *{d}* dropped – consider closing its grid bot.")
    save_current(current)

    if not msgs:
        logging.info("No valid entries.")
        return
    buf=""; chunks=[]
    for m in msgs:
        if len(buf)+len(m)+2>4000: chunks.append(buf); buf=m+"\n\n"
        else: buf+=m+"\n\n"
    if buf: chunks.append(buf)
    for c in chunks: tg(c)

if __name__ == "__main__":
    main()
