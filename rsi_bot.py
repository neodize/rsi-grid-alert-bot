#!/usr/bin/env python3
"""
Telegram RSI bot — CoinGecko (market_chart) edition
"""

import os, sys, time, logging, requests, numpy as np

# --------------------------------------------------------------------------- #
# ░ CONFIG                                                                    #
# --------------------------------------------------------------------------- #
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID",        "YOUR_CHAT_ID")

COINS = {                 # CoinGecko ID → nice symbol
    "bitcoin":     "BTC",
    "ethereum":    "ETH",
    "solana":      "SOL",
    "hyperliquid": "HYPE",
}
VS_CURRENCY      = "usd"  # <—  **fiat or btc/eth**, lower‑case
RSI_PERIOD       = 14
RSI_LOWER, RSI_UPPER = 35, 70

DEBUG = False            # flip to True to print each URL you call
TIMEOUT = 15
session = requests.Session()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")

# --------------------------------------------------------------------------- #
# ░ HELPERS                                                                   #
# --------------------------------------------------------------------------- #
def fetch_prices(coin_id: str, vs_currency: str) -> list[float]:
    """
    Returns a list of close prices (hourly candles, last ≈48 h).
    Falls back to 'usd' once if the first vs_currency is rejected.
    """
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": vs_currency, "days": "2"}
    if DEBUG:
        logging.info("GET %s", session.prepare_request(requests.Request("GET", url, params=params)).url)

    r = session.get(url, params=params, timeout=TIMEOUT)
    if r.status_code == 400 and "invalid vscurrency" in r.text.lower() and vs_currency != "usd":
        logging.warning("'%s' rejected for %s — retrying with usd", vs_currency, coin_id)
        return fetch_prices(coin_id, "usd")

    if r.status_code != 200:
        raise RuntimeError(f"Failed to fetch data for {coin_id}: {r.text}")

    closes = [p[1] for p in r.json().get("prices", [])]
    if len(closes) < RSI_PERIOD + 1:
        raise RuntimeError(f"Not enough data for {coin_id} (got {len(closes)})")
    return closes


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float:
    closes = np.asarray(closes, float)
    deltas = np.diff(closes)

    seed = deltas[:period]
    up   = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs   = up / down if down else 0
    rsi_vals = [100 - (100 / (1 + rs))]

    for d in deltas[period:]:
        up   = (up   * (period - 1) + max(d, 0)) / period
        down = (down * (period - 1) + max(-d, 0)) / period
        rs   = up / down if down else 0
        rsi_vals.append(100 - (100 / (1 + rs)))

    return rsi_vals[-1]


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    session.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=TIMEOUT)

# --------------------------------------------------------------------------- #
# ░ MAIN                                                                      #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    try:
        alerts = []

        for cid, sym in COINS.items():
            try:
                prices = fetch_prices(cid, VS_CURRENCY.lower())
                value  = rsi(prices)

                if value < RSI_LOWER:
                    alerts.append(f"🔻 *{sym}* RSI {value:.2f} — *Oversold*")
                elif value > RSI_UPPER:
                    alerts.append(f"🚀 *{sym}* RSI {value:.2f} — *Overbought*")

            except Exception as e:
                alerts.append(f"❌ Error in RSI Bot for {sym}: {e}")

        send_telegram("\n".join(alerts) if alerts else "✅ No RSI alerts this hour.")

    except Exception as e:
        send_telegram(f"❌ Fatal error in *RSI Bot*: {e}")
        logging.exception(e)
        sys.exit(1)
