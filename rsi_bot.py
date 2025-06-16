"""
Enhanced Grid Scanner – Pionex PERP v5.0.5
Long/Short Only • Top-100 Scan • Cycle ≤ 3 d Filter
"""

import os, logging, requests, math

# ── CONFIG ────────────────────────────────────────────────────
PIONEX      = "https://api.pionex.com"
INTERVAL    = "60M"
LIMIT       = 200
TOP_N       = 100                    # scan top-100 PERPs by 24 h volume
SPACING     = 0.75                   # % grid spacing baseline
GRID_MIN    = 10
GRID_MAX    = 200
CYCLE_MAX   = 3.0                    # max days per full grid cycle

TELE_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELE_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE  = "last_symbols.txt"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE  = {"USDT", "USDC", "BUSD", "DAI"}
EXCL    = {"LUNA", "LUNC", "USTC"}

# ── Helper Filters ───────────────────────────────────────────
def good(sym: str) -> bool:
    u = sym.upper()
    return (u.split("_")[0] not in WRAPPED | STABLE | EXCL
            and not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

# ── Telegram --------------------------------------------------
def tg(text: str):
    if not (TELE_TOKEN and TELE_CHAT):
        return
    url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
    try:
        requests.post(url,
                      json={"chat_id": TELE_CHAT,
                            "text": text,
                            "parse_mode": "Markdown"},
                      timeout=10).raise_for_status()
    except Exception as exc:
        logging.error("Telegram error: %s", exc)

# ── Pionex API ------------------------------------------------
def fetch_symbols():
    r  = requests.get(f"{PIONEX}/api/v1/market/tickers",
                      params={"type": "PERP"}, timeout=10)
    tk = r.json().get("data", {}).get("tickers", [])
    tk_sorted = sorted(tk, key=lambda x: float(x["amount"]), reverse=True)
    return [t["symbol"] for t in tk_sorted if good(t["symbol"])][:TOP_N]

def fetch_klines(symbol: str):
    r = requests.get(f"{PIONEX}/api/v1/market/klines",
                     params={"symbol": symbol,
                             "interval": INTERVAL,
                             "limit": LIMIT,
                             "type": "PERP"},
                     timeout=10)
    kl = r.json().get("data", {}).get("klines") or r.json().get("data")
    if not kl:
        raise RuntimeError("no klines")
    closes = [float(k["close"]) if isinstance(k, dict) else float(k[4])
              for k in kl]
    return closes

# ── Analysis --------------------------------------------------
ZONE_EMO = {
    "Long":  "📈 Entry Zone: 🟢 Long",
    "Short": "📉 Entry Zone: 🔴 Short",
}

def analyse(symbol: str):
    try:
        closes = fetch_klines(symbol)
    except Exception as exc:
        logging.warning("Skip %s: %s", symbol, exc)
        return None

    lo, hi = min(closes), max(closes)
    band   = hi - lo
    now    = closes[-1]
    if band <= 0:
        return None

    pos = (now - lo) / band
    if 0.25 <= pos <= 0.75:          # Neutral → skip
        return None
    zone = "Long" if pos < 0.25 else "Short"

    width_pct = band / now * 100
    grids     = max(GRID_MIN,
                    min(GRID_MAX,
                        int(width_pct / SPACING * 1.2)))

    cycle = (grids * SPACING) / (width_pct + 1e-9) * 2  # estimated days
    cycle = round(cycle, 1)

    if cycle > CYCLE_MAX:            # filter by cycle length
        return None

    fmt = (lambda p: f"${p:.8f}" if p < 0.1 else
                    f"${p:,.4f}" if p < 1   else
                    f"${p:,.2f}")

    return {
        "symbol": symbol,
        "range":  f"{fmt(lo)} – {fmt(hi)}",
        "zone":   zone,
        "grids":  grids,
        "spacing": f"{SPACING:.2f}%",
        "vol":    f"{width_pct:.1f}%",
        "cycle":  cycle,
    }

def build(d: dict) -> str:
    return (f"*{d['symbol']}*\n"
            f"📊 Range: `{d['range']}`\n"
            f"{ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: `{d['grids']}`  |  📏 Spacing: `{d['spacing']}`\n"
            f"🌪️ Volatility: `{d['vol']}`  |  ⏱️ Cycle: `{d['cycle']} days`")

# ── State Helpers --------------------------------------------
def load_last():
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE) as f:
        return set(map(str.strip, f))

def save_current(s):
    with open(STATE_FILE, "w") as f:
        f.write("\n".join(sorted(s)))

# ── Main ------------------------------------------------------
def main():
    symbols = fetch_symbols()
    logging.info("Scanning %d PERPs …", len(symbols))

    results = []
    for sym in symbols:
        res = analyse(sym)
        if res:
            results.append(res)

    # sort by shortest cycle
    results.sort(key=lambda x: x["cycle"])

    current = {r["symbol"] for r in results}
    dropped = load_last() - current

    msgs = [build(r) for r in results]
    msgs += [f"🛑 *{d}* dropped – consider closing its grid bot."
             for d in dropped]
    save_current(current)

    if not msgs:
        tg("No Long/Short grid setups with cycle ≤ 3 days found.")
        return

    buf = ""
    for m in msgs:
        if len(buf) + len(m) + 2 > 4000:
            tg(buf); buf = m + "\n\n"
        else:
            buf += m + "\n\n"
    if buf:
        tg(buf)

if __name__ == "__main__":
    main()
