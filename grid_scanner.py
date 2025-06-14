# grid_scanner.py  ──────────────────────────────────────────────────────────────
# Hourly scan for sideways (grid‑friendly) coins using free CoinGecko API.

import requests, numpy as np, datetime, sys

# ─── USER CONFIG ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID        = "7588547693"

VS               = "usd"
VOLUME_TOP_N     = 40             # evaluate top‑40 by volume
RSI_LOW, RSI_HIGH= 40, 60         # mid‑range
VOL_MIN, VOL_MAX = 0.03, 0.08     # 3‑8 % daily swing
TREND_MAX        = 0.65           # σ₆h / σ₂₄h  < 0.65  ⇒ ranging
EXCLUDE_COINS    = {"BTC", "ETH", "SOL", "HYPE"}  # handled by rsi_bot.py

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def send_telegram(msg:str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg,
                             "parse_mode": "Markdown"}, timeout=10)

def rsi(series, period=14):
    series=np.array(series)
    delta=np.diff(series)
    seed=delta[:period]
    up  = seed[seed>=0].sum()/period
    down= -seed[seed<0].sum()/period or 1e-9
    rs  = up/down
    rsi=[100 - (100/(1+rs))]
    for d in delta[period:]:
        gain=max(d,0); loss=-min(d,0)
        up  = (up*(period-1)+gain)/period
        down= (down*(period-1)+loss)/period or 1e-9
        rs  = up/down
        rsi.append(100 - (100/(1+rs)))
    return round(rsi[-1],2)

def markets():
    url="https://api.coingecko.com/api/v3/coins/markets"
    res=requests.get(url,params={"vs_currency":VS,"order":"volume_desc",
                                  "per_page":250,"page":1},timeout=15)
    res.raise_for_status()
    return res.json()

def closes(cg_id):
    url=f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
    res=requests.get(url,params={"vs_currency":VS,"days":2},timeout=15)
    res.raise_for_status()
    return [p[1] for p in res.json()["prices"]]

# ─── SCAN ──────────────────────────────────────────────────────────────────────
def scan():
    candidates=[]
    for coin in markets()[:VOLUME_TOP_N]:
        symbol=coin["symbol"].upper()
        if symbol in EXCLUDE_COINS:
            continue
        try:
            cls=closes(coin["id"])
            if len(cls)<40: continue
            r     = rsi(cls[-15:])
            vol24 = (max(cls[-24:])-min(cls[-24:]))/cls[-1]
            trend = np.std(cls[-6:])/np.std(cls[-24:]) or 1e-9
            if RSI_LOW<r<RSI_HIGH and VOL_MIN<vol24<VOL_MAX and trend<TREND_MAX:
                candidates.append((symbol,r,vol24,trend))
        except Exception as e:
            print(f"[WARN] skip {symbol}: {e}", file=sys.stderr)
    # rank by lowest trend (rangier) then higher volatility
    candidates.sort(key=lambda x:(x[3],-x[2]))
    return candidates[:5]

# ─── MAIN ───────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    try:
        picks=scan()
        if picks:
            ts=datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
            lines=[f"{sym} — RSI {r:.1f}, vol {v*100:.1f} %, trend {t:.2f}"
                   for sym,r,v,t in picks]
            msg=f"📈 *Hourly Grid Scanner* — {ts}\n\n" + "\n".join(lines)
            send_telegram(msg)
    except Exception as ee:
        send_telegram(f"❌ Grid Scanner error: {ee}")
