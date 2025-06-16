"""Enhanced Grid Scanner – Perp‑Only, Top‑N Output (v2)
-------------------------------------------------------
• Scans high‑volume perpetual pairs on CoinGecko.
• Shows only the top N candidates (default 10) to keep Telegram
  messages well below the 4096‑char limit.
• Each alert still contains: symbol, current price, grid price range,
  entry‑zone tag, and leverage reminder (5‑15×).
• Sends **one Telegram message per coin**, so each stays tiny.
"""

from datetime import datetime, timezone
import os
import requests
from typing import List, Dict

# ──────────────────── CONFIG ──────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

COINGECKO_API = "https://api.coingecko.com/api/v3/coins/markets"
HEADERS       = {"accept": "application/json"}

# Filter universe to these symbols (upper‑case)
MAIN_TOKENS = {"BTC", "ETH", "SOL", "ARB", "DOGE", "ADA", "PEPE", "MATIC", "NEAR", "INJ"}

TOP_N_RESULTS     = 10      # 🔽 change to 7‑10 as desired
MIN_VOL_24H       = 5_000_000
MAX_7D_SPREAD_PCT = 0.35    # skip if (high‑low)/low > 35 %
ENTRY_ZONE_LOW    = 0.30    # 30 % up from band low
ENTRY_ZONE_HIGH   = 0.60    # 60 % up from band low
ABORT_THRESHOLD   = 0.02    # ±2 % band exit (optional watchdog)

# ────────────── TELEGRAM HELPERS ──────────────────

def tg_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Telegram creds missing – message skipped")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Telegram send failed: {e}")


# ─────────────── DATA FETCHERS ────────────────────

def fetch_perp_coins() -> List[Dict]:
    params = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": 100,
        "page": 1,
        "sparkline": "true",
        "price_change_percentage": "24h",
    }
    r = requests.get(COINGECKO_API, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

# ─────────────── ANALYSIS LOGIC ───────────────────

def analyse_coin(c: Dict) -> Dict | None:
    # Skip if not in our perp universe (CoinGecko id check is lenient)
    if c["symbol"].upper() not in MAIN_TOKENS:
        return None

    spark = c.get("sparkline_in_7d", {}).get("price", [])
    if not spark or len(spark) < 20:
        return None

    low, high = min(spark), max(spark)
    spread_pct = (high - low) / low
    if spread_pct > MAX_7D_SPREAD_PCT or c["total_volume"] < MIN_VOL_24H:
        return None

    px = c["current_price"]
    pos = (px - low) / (high - low)
    entry_ok = ENTRY_ZONE_LOW <= pos <= ENTRY_ZONE_HIGH
    entry_tag = "✅ Mid‑range" if entry_ok else "❌ Near edge"

    return {
        "symbol": c["symbol"].upper(),
        "name": c["name"],
        "price": px,
        "low": low,
        "high": high,
        "entry_tag": entry_tag,
        "spread_pct": spread_pct,
    }

# ────────────── ALERT FORMATTING ──────────────────

def fmt_alert(d: Dict) -> str:
    p, lo, hi = map(lambda x: round(x, 4), (d["price"], d["low"], d["high"]))
    return (
        f"📊 *{d['symbol']}*  (Perp 5‑15×)\n"
        f"Price: `${p}`\n"
        f"Range: `${lo}` – `${hi}`\n"
        f"Entry: {d['entry_tag']}\n"
    )

# ──────────────── MAIN SCAN ───────────────────────

def scan_top_n():
    coins = fetch_perp_coins()
    picks = []
    for c in coins:
        info = analyse_coin(c)
        if info:
            picks.append(info)
        if len(picks) >= TOP_N_RESULTS:
            break

    if not picks:
        tg_send("❌ No suitable perp grid candidates found.")
        return

    for d in picks:
        tg_send(fmt_alert(d))

# Optional watchdog stub (price exit alert)

def abort_if_price_out(symbol: str, px: float, low: float, high: float):
    if px > high * (1 + ABORT_THRESHOLD) or px < low * (1 - ABORT_THRESHOLD):
        tg_send(f"🔴 *{symbol}* price {px:.4f} left grid band – consider stopping bot")

# ───────────────────────────────────────────────────
if __name__ == "__main__":
    scan_top_n()
