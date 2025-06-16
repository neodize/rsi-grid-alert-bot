"""
Enhanced Grid Scanner – Pionex PERP Monitor v4.3.2
"""

import os, logging, requests
from datetime import datetime

# ────── CONFIG ──────
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELE_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
PIONEX_API = "https://api.pionex.com"
INTERVAL   = "60M"
TOP_N      = 10
TARGET_SPACING = 0.75   # %
GRID_MIN_SPACING = 0.30 # %

HYPE = "HYPE_USDT_PERP"

WRAPPED = {"WBTC_USDT_PERP", "WETH_USDT_PERP"}
STABLE  = {"USDC_USDT_PERP", "USDT_USDT_PERP", "DAI_USDT_PERP"}
EXCLUDED = {"1000SATS_USDT_PERP", "LUNA_USDT_PERP", "USTC_USDT_PERP"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ────── TELEGRAM ──────
def tg_send(msgs):
    if not TELE_TOKEN or not TELE_CHAT:
        return
    for m in msgs:
        try:
            requests.post(f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage",
                          json={"chat_id": TELE_CHAT, "text": m, "parse_mode": "Markdown"},
                          timeout=10).raise_for_status()
        except Exception as e:
            logging.error("Telegram error: %s", e)

# ────── API HELPERS ──────
def get_top_symbols():
    url = f"{PIONEX_API}/api/v1/market/tickers?type=PERP"
    js  = requests.get(url, timeout=10).json()
    tickers = js.get("data", {}).get("tickers", [])
    sorted_tk = sorted(tickers, key=lambda x: float(x["amount"]), reverse=True)
    symbols = [t["symbol"] for t in sorted_tk if not is_excluded(t["symbol"])]
    return symbols[:TOP_N]

def fetch_klines(symbol):
    url = f"{PIONEX_API}/api/v1/market/klines"
    js  = requests.get(url,
                       params={"symbol": symbol, "interval": INTERVAL, "limit": 200, "type": "PERP"},
                       timeout=10).json()
    if "data" not in js or not js["data"]:
        raise RuntimeError("No klines")
    closes = [float(k["close"]) for k in js["data"]["klines"]]
    highs  = [float(k["high"])  for k in js["data"]["klines"]]
    lows   = [float(k["low"])   for k in js["data"]["klines"]]
    return closes, highs, lows

def is_excluded(sym):
    s = sym.upper()
    return (s in WRAPPED or s in STABLE or s in EXCLUDED or
            s.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def perp2spot(p): return p.replace("_PERP", "")

# ────── ANALYSIS ──────
def analyse(pair):
    spot = perp2spot(pair)
    try:
        closes, highs, lows = fetch_klines(spot)
    except Exception as e:
        logging.warning("Skip %s: %s", pair, e); return None
    if len(closes) < 100: return None
    hi, lo = max(closes), min(closes)
    band = hi - lo
    now = closes[-1]
    if band <= 0: return None
    pos = (now - lo) / band
    if pos < 0.05 or pos > 0.95: return None
    zone = "Long" if pos < 0.25 else "Short" if pos > 0.75 else "Neutral"
    width_pct = band / now * 100
    spacing = max(GRID_MIN_SPACING, TARGET_SPACING)
    grids = max(2, int(width_pct / spacing))
    fmt = lambda p: f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"
    return dict(symbol=pair, zone=zone,
                range=f"{fmt(lo)} – {fmt(hi)}", grids=grids,
                spacing=f"{spacing:.2f}%", vol=f"{width_pct:.1f}%")

def build(d): return (f"*{d['symbol']}*\n"
                      f"Range: `{d['range']}`\n"
                      f"Entry Zone: `{d['zone']}`\n"
                      f"Grids: `{d['grids']}`  |  Spacing: `{d['spacing']}`\n"
                      f"Volatility: `{d['vol']}`")

# ────── MAIN ──────
LAST_FILE = "last_symbols.txt"

def main():
    symbols = get_top_symbols()
    if HYPE not in symbols:
        symbols.append(HYPE)
    current = []
    msgs = []
    for s in symbols:
        res = analyse(s)
        if res:
            current.append(s)
            msgs.append(build(res))
    last = set()
    if os.path.exists(LAST_FILE):
        last = set(line.strip() for line in open(LAST_FILE))
    dropped = [s for s in last if s not in current]
    for d in dropped:
        msgs.append(f"🛑 *{d}* dropped – consider closing its grid bot.")
    open(LAST_FILE, "w").write("\n".join(current))
    if not msgs:
        logging.info("No entries")
        return
    buf = ""
    chunks = []
    for m in msgs:
        if len(buf) + len(m) + 2 > 4000:
            chunks.append(buf)
            buf = m + "\n\n"
        else:
            buf += m + "\n\n"
    if buf:
        chunks.append(buf)
    tg_send(chunks)

if __name__ == "__main__":
    main()
