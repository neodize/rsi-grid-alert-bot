# grid_scanner.py — Trending Coin Scanner for Futures Grid Bots

import requests, numpy as np, datetime

# === USER CONFIG ===
TELEGRAM_TOKEN = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID        = "7588547693"

VS               = "usd"
VOLUME_TOP_N     = 40     # top coins to evaluate
RSI_L, RSI_H     = 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08
TREND_THRESHOLD  = 0.65   # lower = more sideways

# === HELPERS ===

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"[WARN] Telegram failed: {e}")

def calc_rsi(closes, period=14):
    closes = np.array(closes)
    deltas = np.diff(closes)
    seed   = deltas[:period]
    up     = seed[seed >= 0].sum() / period
    down   = -seed[seed < 0].sum() / period or 1e-9
    rs     = up / down
    rsi    = [100 - (100 / (1 + rs))]

    for delta in deltas[period:]:
        gain = max(delta, 0)
        loss = -min(delta, 0)
        up   = (up * (period - 1) + gain) / period
        down = (down * (period - 1) + loss) / period or 1e-9
        rs   = up / down
        rsi.append(100 - (100 / (1 + rs)))

    return round(rsi[-1], 2)

# === SCANNER ===

def get_markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    res = requests.get(url, params={
        "vs_currency": VS,
        "order": "volume_desc",
        "per_page": 250,
        "page": 1
    }, timeout=15)
    return res.json()

def get_closes(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    res = requests.get(url, params={
        "vs_currency": VS,
        "days": 2
    }, timeout=15)
    prices = res.json().get("prices", [])
    return [p[1] for p in prices]

def scan_trending():
    results = []
    for coin in get_markets()[:VOLUME_TOP_N]:
        try:
            closes = get_closes(coin["id"])
            if len(closes) < 40:
                continue
            rsi = calc_rsi(closes[-15:])
            vol = (max(closes[-24:]) - min(closes[-24:])) / closes[-1]
            trend_score = np.std(closes[-6:]) / np.std(closes[-24:]) or 1e-9
            if RSI_L < rsi < RSI_H and VOL_MIN < vol < VOL_MAX and trend_score < TREND_THRESHOLD:
                results.append((coin["symbol"].upper(), rsi, vol, trend_score))
        except Exception as e:
            print(f"[WARN] Skipped {coin['id']}: {e}")
    results.sort(key=lambda x: (x[3], -x[2]))  # prefer low trend, high vol
    return results[:5]

# === MAIN ===

if __name__ == "__main__":
    picks = scan_trending()
    if picks:
        ts = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        lines = [
            f"{sym} — RSI {r:.1f}, vol {v*100:.1f} %, trend {t:.2f}"
            for sym, r, v, t in picks
        ]
        msg = f"📈 *Hourly Grid Scanner* — {ts}\n\n" + "\n".join(lines)
        send_telegram(msg)
