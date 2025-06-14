import requests, numpy as np, datetime
from rsi_bot_helpers import calc_rsi, send_telegram   # reuse helpers

VS = "usd"
VOLUME_RANK_CUTOFF = 40   # top‑40 by volume
RSI_L, RSI_H = 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08

def markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    return requests.get(url, params={"vs_currency":VS,
                                     "order":"volume_desc",
                                     "per_page":250,"page":1}).json()

def closes(id):
    url = f"https://api.coingecko.com/api/v3/coins/{id}/market_chart"
    r = requests.get(url, params={"vs_currency":VS,"days":2})
    return [p[1] for p in r.json()["prices"]]

def scan():
    picks=[]
    for coin in markets()[:VOLUME_RANK_CUTOFF]:
        try:
            c = closes(coin["id"])
            rsi = calc_rsi(c[-15:])
            vol24 = (max(c[-24:])-min(c[-24:]))/c[-1]
            tscore = np.std(c[-6:])/np.std(c[-24:])
            if RSI_L<rsi<RSI_H and VOL_MIN<vol24<VOL_MAX and tscore<0.65:
                picks.append((coin["symbol"].upper(),rsi,vol24,tscore))
        except: pass
    picks.sort(key=lambda x:(x[3],-x[2]))  # low trend, higher vol
    return picks[:5]

if __name__=="__main__":
    best=scan()
    if best:
        ts=datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        lines=[f"{sym} — RSI {r:.1f}, vol {v*100:.1f} %, trend {t:.2f}"
               for sym,r,v,t in best]
        msg=f"📈 *Hourly Grid Scanner* — {ts}\n\n"+"\n".join(lines)
        send_telegram(msg)
