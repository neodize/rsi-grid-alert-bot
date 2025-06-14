# grid_scanner.py  ──────────────────────────────────────────────────────────────
# Hourly scan for sideways (grid‑friendly) coins using CoinGecko free API.

import requests, numpy as np, time, sys
from datetime import datetime, timezone

# ─── USER CONFIG ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID          = "7588547693"

VS               = "usd"
TOP_N_VOL        = 30         # evaluate only top‑30 by volume
MAX_GOOD_COINS   = 5          # stop scanning after 5 matches
EXCLUDE_COINS    = {"BTC","ETH","SOL","HYPE"}

RSI_LOW, RSI_HIGH= 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08
TREND_MAX        = 0.65       # σ₆h / σ₂₄h  (lower = rangier)
REQUEST_DELAY    = 1.5        # seconds between market_chart calls
MAX_RETRIES      = 3          # retries for 429

# ─── HELPER FUNCTIONS ──────────────────────────────────────────────────────────
def send_tg(msg:str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID,
                                 "text": msg,
                                 "parse_mode": "Markdown"},
                      timeout=12)
    except Exception as e:
        print(f"[WARN] Telegram error: {e}", file=sys.stderr)

def rsi(vals, period=14):
    vals = np.array(vals)
    delta = np.diff(vals)
    seed  = delta[:period]
    up    = seed[seed>=0].sum()/period
    dn    = -seed[seed<0].sum()/period or 1e-9
    rs    = up/dn
    rsi_v = 100 - 100/(1+rs)
    for d in delta[period:]:
        gain = max(d,0); loss = -min(d,0)
        up = (up*(period-1)+gain)/period
        dn = (dn*(period-1)+loss)/period or 1e-9
        rs = up/dn
        rsi_v = 100 - 100/(1+rs)
    return round(rsi_v,2)

def markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    res = requests.get(url, params={
        "vs_currency": VS,
        "order": "volume_desc",
        "per_page": 250,
        "page": 1
    }, timeout=20)
    res.raise_for_status()
    return res.json()

def closes(cid):
    url = f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
    for _ in range(MAX_RETRIES):
        r = requests.get(url, params={"vs_currency": VS, "days": 2}, timeout=20)
        if r.status_code == 429:
            time.sleep(3)          # back‑off
            continue
        r.raise_for_status()
        return [p[1] for p in r.json()["prices"]]
    raise ValueError("429 rate‑limit")

# ─── SCAN FUNCTION ─────────────────────────────────────────────────────────────
def scan():
    picks=[]
    for coin in markets()[:TOP_N_VOL]:
        if len(picks) >= MAX_GOOD_COINS:
            break
        sym = coin["symbol"].upper()
        if sym in EXCLUDE_COINS:
            continue
        # skip near‑stable coins
        change = coin.get("price_change_percentage_24h")
        if change is not None and abs(change) < 1:
            continue
        try:
            price_series = closes(coin["id"])
            time.sleep(REQUEST_DELAY)
            if len(price_series) < 40:
                continue
            rsi_val = rsi(price_series[-15:])
            vol24   = (max(price_series[-24:]) - min(price_series[-24:])) / price_series[-1]
            trend   = np.std(price_series[-6:]) / np.std(price_series[-24:]) or 1e-9
            if RSI_LOW < rsi_val < RSI_HIGH and VOL_MIN < vol24 < VOL_MAX and trend < TREND_MAX:
                picks.append((sym, rsi_val, vol24, trend))
        except Exception as e:
            print(f"[WARN] skip {sym}: {e}", file=sys.stderr)
    # sort by lowest trend then higher volatility
    picks.sort(key=lambda x: (x[3], -x[2]))
    return picks[:MAX_GOOD_COINS]

# ─── MAIN ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        results = scan()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        if results:
            lines = [f"{s} — RSI {r:.1f}, vol {v*100:.1f} %, trend {t:.2f}"
                     for s, r, v, t in results]
            send_tg(f"📈 *Hourly Grid Scanner* — {ts}\n\n" + "\n".join(lines))
        else:
            send_tg(f"📉 *Grid Scanner* — {ts}\nNo coins met sideways criteria.")
    except Exception as e:
        send_tg(f"❌ Grid Scanner fatal error: {e}")
