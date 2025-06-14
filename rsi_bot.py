import requests, numpy as np
from datetime import datetime, timezone
from rsi_bot_helpers import rsi, send_telegram

VS = "usd"
RSI_LOW = 30
RSI_HIGH = 70
GRID_MODE = "Arithmetic"
GRID_DIRECTION = "Long"
GRID_TRAILING = "Disabled"

MAIN_COINS = {
    "BTC": {"id": "bitcoin"},
    "ETH": {"id": "ethereum"},
    "SOL": {"id": "solana"},
    "HYPE": {"id": "hyperliquid"},
}

EXCLUDE = {"BTC", "ETH", "SOL", "HYPE", "USDT", "USDC"}

RS_SC_LOW, RS_SC_HIGH = 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08
TREND_MAX = 0.65
SCAN_PICKS = 5

def closes(id):
    url = f"https://api.coingecko.com/api/v3/coins/{id}/market_chart"
    r = requests.get(url, params={"vs_currency": VS, "days": 2})
    r.raise_for_status()
    return [p[1] for p in r.json()["prices"]]

def price_now(id):
    url = f"https://api.coingecko.com/api/v3/simple/price"
    r = requests.get(url, params={"ids": id, "vs_currencies": VS})
    r.raise_for_status()
    return r.json()[id][VS]

def grid_range(prices, step_pct=0.012):
    lo = min(prices[-24:])
    hi = max(prices[-24:])
    return round(lo, 2), round(hi, 2)

def grid_count(price_range, step_pct=0.012):
    low, high = price_range
    step = low * step_pct
    grids = max(3, min(50, int((high - low) / step)))
    return grids

def markets_spark():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": VS,
        "order": "volume_desc",
        "per_page": 250,
        "page": 1,
        "sparkline": "true"
    }
    r = requests.get(url, params=params)
    r.raise_for_status()
    return r.json()

def scan():
    picks = []
    for c in markets_spark():
        sym = c["symbol"].upper()
        if sym in EXCLUDE:
            continue
        closes = c.get("sparkline_in_7d", {}).get("price", [])
        if len(closes) < 48:
            continue
        closes = closes[-48:]
        r_val = rsi(closes[-15:])
        vol = (max(closes[-24:]) - min(closes[-24:])) / closes[-1]
        trend = np.std(closes[-6:]) / np.std(closes[-24:]) or 0
        if RS_SC_LOW < r_val < RS_SC_HIGH and VOL_MIN < vol < VOL_MAX and trend < TREND_MAX:
            picks.append((sym, closes))
        if len(picks) >= SCAN_PICKS:
            break
    return picks

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📉 HOURLY RSI ALERT — {now}"]

    for sym, meta in MAIN_COINS.items():
        try:
            prices = closes(meta["id"])
            r_val = rsi(prices[-15:])
            if r_val < RSI_HIGH:
                grid_lo, grid_hi = grid_range(prices)
                grid_num = grid_count((grid_lo, grid_hi))
                lines += [
                    f"\n🔻 {sym} RSI {r_val:.2f}",
                    f"📊 {sym} Grid Bot Suggestion",
                    f"• Price Range: ${grid_lo} – ${grid_hi}",
                    f"• Grids: {grid_num}",
                    f"• Mode: {GRID_MODE}",
                    f"• Trailing: {GRID_TRAILING}",
                    f"• Direction: {GRID_DIRECTION}",
                ]
        except Exception as e:
            lines.append(f"\n⚠️ {sym} data error: {e}")

    # Grid Scanner Picks
    picks = scan()
    if picks:
        lines.append(f"\n📊 Sideways coins to grid now:")
        for sym, prices in picks:
            grid_lo, grid_hi = grid_range(prices)
            grid_num = grid_count((grid_lo, grid_hi))
            lines += [
                f"\n• {sym}",
                f"  • Price Range: ${grid_lo} – ${grid_hi}",
                f"  • Grids: {grid_num}",
                f"  • Mode: {GRID_MODE}",
                f"  • Trailing: {GRID_TRAILING}",
                f"  • Direction: {GRID_DIRECTION}",
            ]

    send_telegram("\n".join(lines))

if __name__ == "__main__":
    main()
