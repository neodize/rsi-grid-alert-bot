import requests
import logging
from statistics import mean
from datetime import datetime
from math import floor
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TELEGRAM_TOKEN = os.getenv("TG_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")
TOP_N = 10
TARGET_SPACING = 0.75
GRID_MIN_SPACING = 0.3
FEE = 0.05

last_symbols = set()

def get_top_symbols():
    url = "https://api.pionex.com/api/v1/exchange/tickers"
    data = requests.get(url).json()["data"]
    filtered = [d for d in data if d["symbol"].endswith("_USDT") and d.get("symbol_display", "").endswith("PERP")]
    sorted_data = sorted(filtered, key=lambda x: float(x["base_vol"]), reverse=True)
    return [d["symbol"].replace("_USDT", "") for d in sorted_data[:TOP_N]]

def fetch_klines(symbol):
    url = f"https://api.pionex.com/api/v1/market/klines?symbol={symbol}_USDT&interval=60M&limit=200&type=PERP"
    resp = requests.get(url)
    if not resp.ok:
        raise RuntimeError("No klines")
    raw = resp.json()
    if not raw or "data" not in raw or not raw["data"]:
        raise RuntimeError("Empty klines")
    data = raw["data"]
    closes = [float(x[4]) for x in data]
    highs = [float(x[2]) for x in data]
    lows = [float(x[3]) for x in data]
    return closes, highs, lows

def perp_to_spot(symbol):
    return symbol.replace("_PERP", "")

def analyse(pair):
    spot = perp_to_spot(pair)
    try:
        closes, highs, lows = fetch_klines(spot)
    except Exception as e:
        logging.warning("Skip %s: %s", pair, str(e))
        return None

    if len(closes) < 100:
        return None
    hi, lo = max(closes), min(closes)
    band = hi - lo
    if band <= 0:
        return None
    now = closes[-1]
    pos = (now - lo) / band
    if pos < 0.05 or pos > 0.95:
        return None

    if pos < 0.25:
        zone = "Long"
    elif pos > 0.75:
        zone = "Short"
    else:
        zone = "Neutral"

    width_pct = band / now * 100
    spacing = max(GRID_MIN_SPACING, TARGET_SPACING)
    grids = max(2, int(width_pct / spacing))

    def fmt(p):
        if p >= 1: return f"${p:,.2f}"
        if p >= 0.1: return f"${p:,.4f}"
        return f"${p:.8f}"

    return {
        "symbol": pair,
        "zone": zone,
        "range": f"{fmt(lo)} – {fmt(hi)}",
        "grids": grids,
        "spacing": f"{spacing:.2f}%",
        "vol": f"{width_pct:.1f}%"
    }

def format_message(data, stopped=None):
    msg = f"📊 *Grid Scanner*\n_{datetime.now().strftime('%Y-%m-%d %H:%M')}_\n"
    for d in data:
        msg += (
            f"\n*{d['symbol']}*\n"
            f"Zone: `{d['zone']}`\n"
            f"Range: `{d['range']}`\n"
            f"Grid Count: `{d['grids']}`\n"
            f"Spacing: `{d['spacing']}`\n"
            f"Volatility: `{d['vol']}`\n"
        )
    if stopped:
        msg += "\n🛑 *Stop Suggestion*: " + ", ".join(stopped)
    return msg

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except Exception as e:
        logging.error("Telegram send failed: %s", e)

def main():
    global last_symbols
    symbols = get_top_symbols()
    data = []
    for s in symbols:
        res = analyse(s + "_PERP")
        if res:
            data.append(res)

    current_symbols = set([d["symbol"] for d in data])
    stopped = list(last_symbols - current_symbols) if last_symbols else []
    last_symbols = current_symbols

    if data:
        msg = format_message(data, stopped)
        send_telegram(msg)
    else:
        logging.info("No valid entries found.")

if __name__ == "__main__":
    main()
