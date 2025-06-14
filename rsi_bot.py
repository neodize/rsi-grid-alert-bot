#!/usr/bin/env python3
"""
RSI‑alert Telegram bot
================================
Pulls hourly OHLC data from CoinGecko, computes 14‑period RSI,
and notifies you on Telegram when a coin becomes overbought
(RSI > 70) or oversold (RSI < 35).
"""

import os
import time
import requests
import numpy as np

# === CONFIGURATION === -------------------------------------------------------

# (Tip) Store these two in your shell environment instead of hard‑coding
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8")
CHAT_ID        = os.getenv("CHAT_ID",        "7588547693")

COINS = {
    "bitcoin":     "BTC",
    "ethereum":    "ETH",
    "solana":      "SOL",
    "hyperliquid": "HYPE",       # CoinGecko ID for Hype is “hyperliquid”
}

VS_CURRENCY = "USDT"             # Use any currency you like; case‑insensitive
RSI_PERIOD  = 14
RSI_LOWER   = 35
RSI_UPPER   = 70

REQUEST_TIMEOUT = 15             # seconds
SESSION = requests.Session()     # reusable HTTP connection


# === HELPER FUNCTIONS === ----------------------------------------------------

def validate_vs_currency(vs_currency: str) -> str:
    """
    Make sure the chosen vs_currency is supported by CoinGecko.
    Returns the lower‑case version if valid, otherwise raises ValueError.
    """
    url = "https://api.coingecko.com/api/v3/simple/supported_vs_currencies"
    try:
        supported = SESSION.get(url, timeout=REQUEST_TIMEOUT).json()
    except Exception as exc:
        raise RuntimeError(f"Could not fetch supported vs_currencies list: {exc}")

    lc = vs_currency.lower()
    if lc not in supported:
        raise ValueError(f"'{vs_currency}' is not in CoinGecko's supported vs_currencies list.")
    return lc


def fetch_ohlc_from_coingecko(coin_id: str, vs_currency: str) -> list[float]:
    """
    Returns a list of closing prices (hourly candles, 48+ values).
    """
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {
        "vs_currency": vs_currency,   # already lower‑cased & validated
        "days": "2"                   # → hourly data for the last ~48 h
    }
    r = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Failed to fetch data for {coin_id}: {r.text}")

    closes = [price[1] for price in r.json().get("prices", [])]
    if len(closes) < RSI_PERIOD + 1:
        raise RuntimeError(f"Not enough data to calculate RSI for {coin_id} (got {len(closes)})")
    return closes


def calculate_rsi(closes: list[float], period: int = RSI_PERIOD) -> float:
    """
    Vectorised, running‑average RSI implementation.
    """
    closes = np.asarray(closes, dtype=float)
    deltas = np.diff(closes)

    seed = deltas[:period]
    up   = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs   = up / down if down != 0 else 0.0
    rsi  = [100.0 - (100.0 / (1.0 + rs))]

    for delta in deltas[period:]:
        upval   = max(delta, 0.0)
        downval = -min(delta, 0.0)
        up   = (up * (period - 1) + upval)   / period
        down = (down * (period - 1) + downval) / period
        rs   = up / down if down != 0 else 0.0
        rsi.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi[-1]


def send_telegram_message(text: str) -> bool:
    """
    Sends a Markdown‑formatted message. Returns True on success.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":   CHAT_ID,
        "text":      text,
        "parse_mode": "Markdown",
    }
    try:
        r = SESSION.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


# === MAIN EXECUTION === ------------------------------------------------------

if __name__ == "__main__":
    try:
        # 1) Ensure vs_currency is valid and force lower‑case
        vs_curr = validate_vs_currency(VS_CURRENCY)   # raises if invalid

        # 2) Build all alert lines first, then send one composite message
        alerts: list[str] = []

        for coin_id, symbol in COINS.items():
            try:
                closes = fetch_ohlc_from_coingecko(coin_id, vs_curr)
                rsi    = calculate_rsi(closes)

                if rsi < RSI_LOWER:
                    alerts.append(f"🔻 *{symbol}* RSI {rsi:.2f} — *Oversold*")
                elif rsi > RSI_UPPER:
                    alerts.append(f"🚀 *{symbol}* RSI {rsi:.2f} — *Overbought*")

            except Exception as exc:
                alerts.append(f"❌ Error in RSI Bot for {symbol}: {exc}")

        if not alerts:
            alerts.append("✅ No RSI alerts this hour.")

        # 3) Fire the Telegram message
        if not send_telegram_message("\n".join(alerts)):
            raise RuntimeError("Telegram API request failed")

    except Exception as exc:
        # Top‑level catch to ensure you are notified of unexpected crashes
        send_telegram_message(f"❌ Error in *RSI Bot*: {exc}")
