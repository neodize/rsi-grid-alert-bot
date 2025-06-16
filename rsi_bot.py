import requests
import numpy as np
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

WEBHOOK_URL = "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/sendMessage"
CHAT_ID = "<YOUR_CHAT_ID>"

# Store last symbols shown
LAST_SYMBOLS_FILE = "last_symbols.txt"

def get_top_symbols(limit=15):
    url = "https://api.pionex.com/api/v1/market/getMarket24hList"
    try:
        resp = requests.get(url).json()
        top = sorted(resp, key=lambda x: float(x["amount"]), reverse=True)
        return [t["symbol"] for t in top if t["symbol"].endswith("_USDT") and "PERP" not in t["symbol"]][:limit]
    except Exception as e:
        logging.error(f"Failed to get top symbols: {e}")
        return []

def fetch_klines(symbol):
    url = f"https://api.pionex.com/api/v1/market/klines?symbol={symbol}&interval=60M&limit=200&type=PERP"
    try:
        res = requests.get(url).json()
        if isinstance(res, list) and res:
            closes = [float(x[4]) for x in res]
            highs = [float(x[2]) for x in res]
            lows = [float(x[3]) for x in res]
            return closes, highs, lows
        else:
            raise RuntimeError("No klines")
    except Exception as e:
        logging.warning(f"Skip {symbol}: {e}")
        return None, None, None

def calculate_rsi(data, period=14):
    deltas = np.diff(data)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = [100. - 100. / (1. + rs)]
    for delta in deltas[period:]:
        upval = max(delta, 0)
        downval = -min(delta, 0)
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi.append(100. - 100. / (1. + rs))
    return rsi

def estimate_grid_config(price, grids=30, spacing_pct=0.5, fee_pct=0.05):
    spacing = price * spacing_pct / 100
    total_range = spacing * grids
    low = price - total_range / 2
    high = price + total_range / 2
    expected_profit_per_grid = spacing * (1 - fee_pct / 100)
    expected_cycles = (price - low) / spacing
    return low, high, expected_profit_per_grid, expected_cycles

def send_telegram(message):
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(WEBHOOK_URL, json=payload)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")

def load_last_symbols():
    try:
        with open(LAST_SYMBOLS_FILE, "r") as f:
            return f.read().splitlines()
    except:
        return []

def save_last_symbols(symbols):
    with open(LAST_SYMBOLS_FILE, "w") as f:
        f.write("\n".join(symbols))

def analyse(symbol):
    closes, highs, lows = fetch_klines(symbol)
    if closes is None:
        return None

    rsi = calculate_rsi(closes)
    current_price = closes[-1]
    rsi_latest = rsi[-1]

    low, high, profit, cycles = estimate_grid_config(current_price)

    decimals = 6 if current_price < 1 else 2
    return {
        "symbol": symbol,
        "rsi": round(rsi_latest, 2),
        "price": round(current_price, decimals),
        "entry_low": round(low, decimals),
        "entry_high": round(high, decimals),
        "profit_per_grid": round(profit, decimals),
        "grid_count": 30,
        "spacing_pct": 0.5,
        "fee_pct": 0.05,
        "expected_cycles": round(cycles)
    }

def format_message(data_list, stop_list):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"📈 *Grid Trading Candidates* — `{now}`\n\n"
    for d in data_list:
        msg += (
            f"*{d['symbol']}*\n"
            f"Price: `{d['price']}`\n"
            f"RSI: `{d['rsi']}`\n"
            f"Entry: `{d['entry_low']} - {d['entry_high']}`\n"
            f"Grid: `{d['grid_count']} x {d['spacing_pct']}%` (fee: {d['fee_pct']}%)\n"
            f"Per Grid Profit (est.): `{d['profit_per_grid']}`\n"
            f"Expected Cycles: `{d['expected_cycles']}`\n\n"
        )
    if stop_list:
        msg += "🛑 *Stop Suggestion*: " + ", ".join(stop_list)
    return msg

def main():
    symbols = get_top_symbols()
    if not symbols:
        logging.info("No symbols fetched")
        return

    last_symbols = load_last_symbols()
    stop_symbols = [s for s in last_symbols if s not in symbols]

    results = []
    for s in symbols:
        res = analyse(s)
        if res:
            results.append(res)

    if results:
        msg = format_message(results, stop_symbols)
        send_telegram(msg)
        save_last_symbols(symbols)
    else:
        logging.info("No valid entries found.")

if __name__ == "__main__":
    main()
