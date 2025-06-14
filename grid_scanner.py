# grid_scanner.py  ──────────────────────────────────────────────────────────────
import requests, numpy as np, datetime, time, sys

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID          = "7588547693"

VS               = "usd"
TOP_N_VOL        = 40
EXCLUDE_COINS    = {"BTC","ETH","SOL","HYPE"}
RSI_L, RSI_H     = 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08
TREND_MAX        = 0.65
REQUEST_DELAY    = 0.8        # seconds between CoinGecko calls
MAX_RETRIES      = 3

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def send_tg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url,data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"Markdown"},timeout=10)
    except Exception as e:
        print(f"[WARN] Telegram error: {e}", file=sys.stderr)

def rsi(vals, period=14):
    v=np.array(vals); d=np.diff(v)
    seed=d[:period]; up=seed[seed>=0].sum()/period; dn=-seed[seed<0].sum()/period or 1e-9
    rs=up/dn; r=[100-100/(1+rs)]
    for delta in d[period:]:
        g=max(delta,0); l=-min(delta,0)
        up=(up*(period-1)+g)/period; dn=(dn*(period-1)+l)/period or 1e-9
        rs=up/dn; r.append(100-100/(1+rs))
    return round(r[-1],2)

def markets():
    url="https://api.coingecko.com/api/v3/coins/markets"
    r=requests.get(url,params={"vs_currency":VS,"order":"volume_desc","per_page":250,"page":1},timeout=20)
    r.raise_for_status(); return r.json()

def closes(cid):
    url=f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
    for attempt in range(MAX_RETRIES):
        r=requests.get(url,params={"vs_currency":VS,"days":2},timeout=15)
        if r.status_code==429:
            time.sleep(2)      # simple back‑off
            continue
        r.raise_for_status()
        return [p[1] for p in r.json()["prices"]]
    raise ValueError("429 rate‑limit")

# ─── SCAN ──────────────────────────────────────────────────────────────────────
def scan():
    picks=[]
    for coin in markets()[:TOP_N_VOL]:
        sym=coin["symbol"].upper()
        if sym in EXCLUDE_COINS: continue
        # skip obvious stables / dead coins
        if coin.get("price_change_percentage_24h") is not None:
            if abs(coin["price_change_percentage_24h"]) < 1:  # ≈ stable
                continue
        try:
            cls=closes(coin["id"]); time.sleep(REQUEST_DELAY)
            if len(cls)<40: continue
            r=rsi(cls[-15:]); vol=(max(cls[-24:])-min(cls[-24:]))/cls[-1]
            trend=np.std(cls[-6:])/np.std(cls[-24:]) or 1e-9
            if RSI_L<r<RSI_H and VOL_MIN<vol<VOL_MAX and trend<TREND_MAX:
                picks.append((sym,r,vol,trend))
        except Exception as e:
            print(f"[WARN] skip {sym}: {e}", file=sys.stderr)
    picks.sort(key=lambda x:(x[3],-x[2]))
    return picks[:5]

# ─── MAIN ──────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    try:
        out=scan()
        ts=datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        if out:
            lines=[f"{s} — RSI {r:.1f}, vol {v*100:.1f} %, trend {t:.2f}"
                   for s,r,v,t in out]
            send_tg(f"📈 *Hourly Grid Scanner* — {ts}\n\n"+"\n".join(lines))
        else:
            send_tg(f"📉 *Grid Scanner* — {ts}\nNo coins met sideways criteria.")
    except Exception as e:
        send_tg(f"❌ Grid Scanner fatal error: {e}")
