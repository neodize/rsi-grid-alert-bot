"""
Enhanced Grid Scanner – Pionex PERP v5.0.3
"""

import os, logging, requests

PIONEX = "https://api.pionex.com"
INTERVAL = "60M"
LIMIT = 200
TOP_N = 10
GRID_TARGET_SPACING = 0.75
GRID_MIN_SPACING = 0.35

TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELE_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = "last_symbols.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE = {"USDT", "USDC", "BUSD", "DAI"}
EXCL = {"LUNA", "LUNC", "USTC"}

def good(sym: str) -> bool:
    u = sym.upper()
    return (
        u.split("_")[0] not in WRAPPED | STABLE | EXCL
        and not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S"))
    )

def tg(text: str):
    if not TELE_TOKEN or not TELE_CHAT:
        return
    url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": TELE_CHAT, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        logging.error("Telegram error: %s", e)

def top_perp_symbols():
    url = f"{PIONEX}/api/v1/market/tickers"
    js = requests.get(url, params={"type": "PERP"}, timeout=10).json()
    tickers = js.get("data", {}).get("tickers", [])
    sorted_tk = sorted(tickers, key=lambda x: float(x["amount"]), reverse=True)
    symbols = [t["symbol"] for t in sorted_tk if good(t["symbol"])]
    return symbols[:TOP_N]

def fetch_klines(spot: str):
    url = f"{PIONEX}/api/v1/market/klines"
    js = requests.get(
        url,
        params={
            "symbol": spot,
            "interval": INTERVAL,
            "limit": LIMIT,
            "type": "PERP",
        },
        timeout=10,
    ).json()
    kl = js.get("data", {}).get("klines") or js.get("data")
    if not kl:
        raise RuntimeError("no klines")
    closes = [float(k["close"]) if isinstance(k, dict) else float(k[4]) for k in kl]
    return closes

def analyse(perp: str):
    spot = perp.replace("_PERP", "")
    try:
        closes = fetch_klines(spot)
    except Exception as e:
        logging.warning("Skip %s: %s", perp, e)
        return None
    lo, hi = min(closes), max(closes)
    band = hi - lo
    now = closes[-1]
    if band <= 0:
        return None
    pos = (now - lo) / band
    zone = "Long" if pos < 0.25 else "Short" if pos > 0.75 else "Neutral"
    width_pct = band / now * 100

    # 🚫 Skip Neutral zones if volatility is too low
    if zone == "Neutral" and width_pct < 8:
        return None

    spacing = max(GRID_MIN_SPACING, GRID_TARGET_SPACING)
    grids = max(2, int(width_pct / spacing))
    cycle_days = round((grids * spacing) / width_pct * 2, 1) if width_pct else "-"
    fmt = lambda p: f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"
    return {
        "symbol": perp,
        "range": f"{fmt(lo)} – {fmt(hi)}",
        "zone": zone,
        "grids": grids,
        "spacing": f"{spacing:.2f}%",
        "vol": f"{width_pct:.1f}%",
        "cycle": f"{cycle_days} days",
    }

# emoji mapping
ZONE_EMO = {
    "Long": "📈 Entry Zone: 🟢 Long",
    "Neutral": "🔁 Entry Zone: ⚪️ Neutral",
    "Short": "📉 Entry Zone: 🔴 Short",
}

def build(d):
    return (
        f"*{d['symbol']}*\n"
        f"📊 Range: `{d['range']}`\n"
        f"{ZONE_EMO[d['zone']]}\n"
        f"🧮 Grids: `{d['grids']}`  |
