import os
import requests
import logging
from datetime import datetime
from utils import send_telegram_message, format_coin_output, fetch_klines, estimate_grid_cycles_per_day

# Load ENV vars
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SCAN_MODE = os.getenv("SCAN_MODE", "conservative")

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

def get_coin_list():
    url = "https://api.pionex.com/api/v1/market/ticker"
    r = requests.get(url)
    data = r.json()
    return [
        {
            "symbol": x["symbol"],
            "symbol_raw": x["symbol"].replace("_USDT", "-USDT"),
            "last": float(x["last"]),
            "vol": float(x["baseVolume"]),
        }
        for x in data["data"]
        if x["symbol"].endswith("_USDT") and "PERP" in x["symbol"]
    ]

def is_valid_grid_candidate(width_pct, vol, est_cycles_per_day, mode="conservative"):
    if mode == "conservative":
        return (
            5 <= width_pct <= 15 and
            vol >= 10_000_000 and
            est_cycles_per_day >= 1.0
        )
    elif mode == "aggressive":
        return (
            3 <= width_pct <= 25 and
            vol >= 3_000_000 and
            est_cycles_per_day >= 0.5
        )
    return False

def analyze():
    results = []
    coins = get_coin_list()
    for info in coins:
        try:
            closes, highs, lows = fetch_klines(info["symbol_raw"], "1h", 200)
            width_pct = (max(highs[-20:]) - min(lows[-20:])) / info["last"] * 100
            est_cycles = estimate_grid_cycles_per_day(closes)

            if is_valid_grid_candidate(width_pct, info["vol"], est_cycles, SCAN_MODE):
                info.update({
                    "bb_width": round(width_pct, 2),
                    "est_cycles_per_day": round(est_cycles, 1)
                })
                results.append(info)
        except Exception as e:
            logging.warning(f"Error processing {info['symbol']}: {e}")
    return sorted(results, key=lambda x: -x["est_cycles_per_day"])

def split_chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

def main():
    logging.info("Scanning Pionex perpetual contracts…")
    candidates = analyze()

    if not candidates and SCAN_MODE == "conservative":
        logging.info("No coins passed conservative scan. Retrying in aggressive mode…")
        global SCAN_MODE
        SCAN_MODE = "aggressive"
        candidates = analyze()

        if not candidates:
            send_telegram_message("⚠️ No suitable grid candidates found even in aggressive mode.")
            return
        else:
            send_telegram_message("⚠️ Conservative scan found nothing. Showing aggressive-mode picks:")

    top_candidates = candidates[:10]  # max 10
    chunks = list(split_chunks(top_candidates, 3))

    for idx, chunk in enumerate(chunks):
        text = "\n\n".join([format_coin_output(x) for x in chunk])
        send_telegram_message(f"📊 Grid Bot Picks (Part {idx+1}/{len(chunks)}):\n\n{text}")

if __name__ == "__main__":
    main()
