# sparkline_grid_scanner.py  ────────────────────────────────────────────────────
# One‑call CoinGecko scanner using sparkline data (no 429 errors).

import requests, numpy as np, datetime
from datetime import timezone

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID        = "7588547693"

VS                 = "usd"
TOP_N_VOL          = 50        # how many high‑volume coins to inspect
MAX_PICKS          = 5
EXCLUDE_SYMBOLS    = {"BTC","ETH","SOL","HYPE"}  # already handled elsewhere

RSI_LOW, RSI_HIGH  = 40, 60
VOL_MIN, VOL_MAX   = 0.03, 0.08
TREND_MAX          = 0.65      # σ₆h / σ₂₄h (lower = rangier)

# ─── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_tg(text:str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID,
                             "text": text,
                             "parse_mode":"Markdown"},
                  timeout=10)

# ─── RSI ───────────────────────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    closes=np.array(closes)
    delta=np.diff(closes)
    seed=delta[:period]
    up  = seed[seed>=0].sum()/period
    dn  = -seed[seed<0].sum()/period or 1e-9
    rs  = up/dn
    rsi = 100-100/(1+rs)
    for d in delta[period:]:
        gain=max(d,0); loss=-min(d,0)
        up=(up*(period-1)+gain)/period
        dn=(dn*(period-1)+loss)/period or 1e-9
        rs=up/dn
        rsi = 100-100/(1+rs)
    return round(rsi,2)

# ─── MAIN SCAN ─────────────────────────────────────────────────────────────────
def scan():
    url="https://api.coingecko.com/api/v3/coins/markets"
    r=requests.get(url, params={
        "vs_currency": VS,
        "order": "volume_desc",
        "per_page": TOP_N_VOL,
        "page": 1,
        "sparkline": "true",
        "price_change_percentage": "24h"
    }, timeout=20)
    r.raise_for_status()
    coins=r.json()

    picks=[]
    for coin in coins:
        sym=coin["symbol"].upper()
        if sym in EXCLUDE_SYMBOLS: continue
        spark=coin.get("sparkline_in_7d",{}).get("price",[])
        if len(spark)<48: continue          # need 48 hourly points
        closes=spark[-48:]                  # last 48h
        rsi=calc_rsi(closes[-15:])
        vol=(max(closes[-24:])-min(closes[-24:]))/closes[-1]
        trend=np.std(closes[-6:])/np.std(closes[-24:]) or 1e-9
        if RSI_LOW<rsi<RSI_HIGH and VOL_MIN<vol<VOL_MAX and trend<TREND_MAX:
            picks.append((sym,rsi,vol,trend))
        if len(picks)>=MAX_PICKS:
            break

    picks.sort(key=lambda x:(x[3],-x[2]))  # sort after break for consistency
    return picks

if __name__=="__main__":
    try:
        best=scan()
        ts=datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        if best:
            lines=[f"{s} — RSI {r:.1f}, vol {v*100:.1f} %, trend {t:.2f}"
                   for s,r,v,t in best]
            send_tg(f"📈 *Hourly Grid Scanner* — {ts}\n\n" + "\n".join(lines))
        else:
            send_tg(f"📉 *Grid Scanner* — {ts}\nNo coins met sideways criteria.")
    except Exception as e:
        send_tg(f"❌ Grid Scanner fatal error: {e}")
