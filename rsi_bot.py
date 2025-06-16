# rsi_bot.py (Enhanced Leaderboard Strategy Edition v5.1)
# Dynamic grids + tight cycle + auto stop alerts

import requests, os, json, logging, math, time

# === CONFIG =================================================
API_BASE     = "https://api.pionex.com/api/v1"
INTERVAL     = "60M"
LIMIT        = 200
TOP_N        = 100          # scan top 100 PERPs by volume
SPACING_MIN  = 0.3          # %   lower bound on grid spacing
SPACING_MAX  = 1.2          # %   upper bound
CYCLE_MAX    = 2.0          # days – only keep setups faster than this
STOP_BUFFER  = 0.01         # 1 % buffer outside range before stop
GRID_MIN     = 10
GRID_MAX     = 200
STATE_FILE   = "active_grids.json"

TG_TOKEN   = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE  = {"USDT", "USDC", "BUSD", "DAI"}
EXCL    = {"LUNA", "LUNC", "USTC"}

# === TELEGRAM ===============================================

def tg(text: str):
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID,
                                  "text": text,
                                  "parse_mode": "Markdown"}, timeout=10)
    except Exception as exc:
        logging.error("Telegram error: %s", exc)

# === PIONEX API =============================================

def good(sym):
    u = sym.upper()
    return (u.split("_")[0] not in WRAPPED | STABLE | EXCL and
            not u.endswith(("UP","DOWN","3L","3S","5L","5S")))

def fetch_symbols():
    r = requests.get(f"{API_BASE}/market/tickers", params={"type":"PERP"}, timeout=10)
    tks = r.json().get("data", {}).get("tickers", [])
    tks = sorted(tks, key=lambda x: float(x["amount"]), reverse=True)
    return [t["symbol"] for t in tks if good(t["symbol"])][:TOP_N]

def fetch_klines(sym):
    r = requests.get(f"{API_BASE}/market/klines",
                     params={"symbol": sym, "interval": INTERVAL, "limit": LIMIT, "type":"PERP"},
                     timeout=10)
    kl = r.json().get("data", {}).get("klines") or r.json().get("data")
    if not kl:
        raise RuntimeError("no klines")
    closes = [float(k["close"]) if isinstance(k, dict) else float(k[4]) for k in kl]
    return closes

# === ANALYSIS ===============================================

ZONE_EMO = {"Long": "📈 Entry Zone: 🟢 Long", "Short": "📉 Entry Zone: 🔴 Short"}

def analysis(sym):
    cls = fetch_klines(sym)
    lo, hi = min(cls), max(cls)
    rng = hi - lo
    now = cls[-1]
    if rng <= 0:
        return None

    pos = (now - lo) / rng
    if 0.25 <= pos <= 0.75:
        return None  # neutral skipped
    zone = "Long" if pos < 0.25 else "Short"

    vol_pct = rng / now * 100
    spacing = max(SPACING_MIN, min(SPACING_MAX, 0.75 * (30 / max(vol_pct, 1))))
    grids = max(GRID_MIN, min(GRID_MAX, math.floor(rng / (now * spacing / 100))))

    cycle = round((grids * spacing) / (vol_pct + 1e-9) * 2, 1)
    if cycle > CYCLE_MAX:
        return None

    fmt = lambda p: f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"

    return {
        "symbol": sym,
        "zone": zone,
        "low": lo,
        "high": hi,
        "now": now,
        "grids": grids,
        "spacing": round(spacing, 2),
        "vol": round(vol_pct, 1),
        "cycle": cycle,
    }

# === STATE I/O ==============================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)

def save_state(d):
    with open(STATE_FILE, "w") as f:
        json.dump(d, f, indent=2)

# === FORMATTERS =============================================

def fmt_range(lo, hi):
    fmt = lambda p: f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"
    return f"{fmt(lo)} – {fmt(hi)}"

def build_start(d):
    lev_hint = "20x–50x" if d['spacing'] <= 0.5 else "10x–25x" if d['spacing'] <= 0.75 else "5x–15x"
    return (f"📈 Start Grid Bot: {d['symbol']}\n"
            f"📊 Range: {fmt_range(d['low'], d['high'])}\n"
            f"{ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: {d['grids']}  |  📏 Spacing: {d['spacing']}%\n"
            f"🌪️ Volatility: {d['vol']}%  |  ⏱️ Cycle: {d['cycle']} days\n"
            f"⚙️ Leverage Hint: {lev_hint}\n")

def build_stop(sym, reason, info):
    return (f"🛑 Exit Alert: {sym}\n"
            f"📉 Reason: {reason}\n"
            f"📊 Range: {fmt_range(info['low'], info['high'])}\n"
            f"💱 Current Price: ${info['now']:,.4f if info['now']<1 else ',.2f'}\n")

# === MAIN ====================================================

def main():
    prev_state = load_state()      # {symbol: {zone, low, high}}
    next_state = {}
    start_msgs, stop_msgs = [], []

    for sym in fetch_symbols():
        res = analysis(sym)
        if not res:
            continue
        next_state[sym] = {
            "zone": res['zone'],
            "low": res['low'],
            "high": res['high']
        }
        # --- start alert ---
        if sym not in prev_state:
            start_msgs.append(build_start(res))
        else:
            # compare for zone flip or range break
            prev = prev_state[sym]
            if prev['zone'] != res['zone']:
                stop_msgs.append(build_stop(sym, f"Trend flip ({prev['zone']} → {res['zone']})", res))
            elif res['now'] > prev['high'] * (1 + STOP_BUFFER) or res['now'] < prev['low'] * (1 - STOP_BUFFER):
                stop_msgs.append(build_stop(sym, "Price exited range", res))

    # any symbols that disappeared entirely
    dropped = set(prev_state) - set(next_state)
    for d in dropped:
        info = prev_state[d]
        dummy_price = (info['low'] + info['high']) / 2
        stop_msgs.append(build_stop(d, "No longer meets criteria", {**info, "now": dummy_price}))

    save_state(next_state)

    # --- send Telegram chunks ---
    for bucket in (start_msgs, stop_msgs):
        if not bucket:
            continue
        buf = ""
        for m in bucket:
            if len(buf) + len(m) + 2 > 4000:
                tg(buf); buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            tg(buf)

if __name__ == "__main__":
    main()
