#!/usr/bin/env python3
"""
RSI alert bot (CoinGecko → Telegram) — bullet‑proof vs_currency handling
"""
import os, sys, logging, requests, numpy as np

# ── CONFIG ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID",        "YOUR_CHAT_ID")

COINS = {
    "bitcoin":     "BTC",
    "ethereum":    "ETH",
    "solana":      "SOL",
    "hyperliquid": "HYPE",
}

RAW_VS_CURN = os.getenv("VS_CURRENCY", "usd")     # keep whatever comes from env
VS_CURRENCY = RAW_VS_CURN.strip().lower()         # ← crucial: strip + lower
RSI_PERIOD  = 14
RSI_LOW, RSI_HIGH = 35, 70
TIMEOUT = 15
DEBUG = True                                      # flip to False once all good
session = requests.Session()

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s"
)
logging.info("Using vs_currency=%s (raw=%r)", VS_CURRENCY, RAW_VS_CURN)

# ── HELPERS ───────────────────────────────────────────────────────────────
def fetch_prices(coin: str) -> list[float]:
    url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
    params = {"vs_currency": VS_CURRENCY, "days": "2"}

    prepared = session.prepare_request(requests.Request("GET", url, params=params))
    if DEBUG:
        logging.debug("→ %s", prepared.url)

    r = session.send(prepared, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code} {r.text}")

    prices = [p[1] for p in r.json().get("prices", [])]
    if len(prices) < RSI_PERIOD + 1:
        raise RuntimeError("not enough data")
    return prices


def rsi(series: list[float], period: int = RSI_PERIOD) -> float:
    closes = np.asarray(series, float)
    deltas = np.diff(closes)

    seed = deltas[:period]
    up   = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs   = up / down if down else 0
    rsi_vals = [100 - 100 / (1 + rs)]

    for d in deltas[period:]:
        up   = (up * (period - 1) + max(d, 0)) / period
        down = (down * (period - 1) + max(-d, 0)) / period
        rs   = up / down if down else 0
        rsi_vals.append(100 - 100 / (1 + rs))
    return rsi_vals[-1]


def telegram(msg: str):
    session.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=TIMEOUT,
    )

# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    alerts = []
    try:
        for cid, symbol in COINS.items():
            try:
                close_prices = fetch_prices(cid)
                val = rsi(close_prices)

                if val < RSI_LOW:
                    alerts.append(f"🔻 *{symbol}* RSI {val:.2f} — Oversold")
                elif val > RSI_HIGH:
                    alerts.append(f"🚀 *{symbol}* RSI {val:.2f} — Overbought")
            except Exception as e:
                alerts.append(f"❌ {symbol}: {e}")

        telegram("\n".join(alerts) if alerts else "✅ No RSI alerts this hour.")

    except Exception as e:
        telegram(f"❌ Fatal in RSI bot: {e}")
        logging.exception(e)
        sys.exit(1)
