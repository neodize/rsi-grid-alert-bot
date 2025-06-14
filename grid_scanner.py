import requests, numpy as np, datetime
from rsi_bot_helpers import calc_rsi, send_telegram  # reuse helpers

VS = "usd"
VOLUME_RANK_CUTOFF = 40
RSI_L, RSI_H = 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08
EXCLUDE = {"btc", "eth", "sol", "hype"}

def markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": VS,
        "order": "volume_desc",
        "per_page": 50,
        "page": 1,
        "sparkline": "true",
        "price_change_percentage": "24h"
    }
    return requests.get(url, params=params).json()

def scan():
    picks = []
    for coin in markets()[:VOLUME_RANK_CUTOFF]:
        symbol = coin["symbol"].lower()
        if symbol in EXCLUDE: continue
        spark = coin.get("sparkline_in_7d", {}).get("price", [])
        if len(spark) < 48: continue  # ~48 1-hour points
        closes = spark[-48:]
        rsi = calc_rsi(closes[-15:])
        vol24 = (max(closes[-24:]) - min(closes[-24:])) / closes[-1]
        tscore = np.std(closes[-6:]) / np.std(closes[-24:])
        if RSI_L < rsi < RSI_H and VOL_MIN < vol24 < VOL_MAX and tscore < 0.65:
            picks.append((coin["symbol"].upper(), rsi, vol24, tscore))
    picks.sort(key=lambda x: (x[3], -x[2]))  # low trend, high volatility
    return picks[:5]

if __name__ == "__main__":
    best = scan()
    if best:
        ts = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        lines = [
            f"{sym} — RSI {r:.1f}, vol {v*100:.1f} %, trend {t:.2f}"
            for sym, r, v, t in best
        ]
        msg = f"📈 *Hourly Grid Scanner* — {ts}\n\n" + "\n".join(lines)
        send_telegram(msg)
