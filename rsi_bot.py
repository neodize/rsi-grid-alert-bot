# rsi_bot.py  ───────────────────────────────────────────────────────────────────
# Sends RSI alerts + grid‑bot suggestions for selected coins.

import requests, numpy as np
from datetime import datetime, timezone
import sys

# ─── TELEGRAM (your credentials) ───────────────────────────────────────────────
TELEGRAM_TOKEN  = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
TELEGRAM_CHATID = "7588547693"          # your private chat ID

# ─── CONFIG ────────────────────────────────────────────────────────────────────
VS   = "usd"
COINS = {                                # CoinGecko ID → symbol
    "bitcoin"     : "BTC",
    "ethereum"    : "ETH",
    "solana"      : "SOL",
    "dogwifcoin"  : "WIF",
    "pepe"        : "PEPE"
}
RSI_LOW  = 35
RSI_HIGH = 65

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def send_tg(msg:str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url,
                      data={"chat_id": TELEGRAM_CHATID,
                            "text": msg,
                            "parse_mode": "Markdown"},
                      timeout=12).raise_for_status()
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}", file=sys.stderr)

def calc_rsi(closes, period=14):
    closes=np.array(closes)
    delta=np.diff(closes)
    seed=delta[:period]
    up=seed[seed>=0].sum()/period
    dn=-seed[seed<0].sum()/period or 1e-9
    rs=up/dn
    rsi=100-100/(1+rs)
    for d in delta[period:]:
        gain=max(d,0); loss=-min(d,0)
        up=(up*(period-1)+gain)/period
        dn=(dn*(period-1)+loss)/period or 1e-9
        rs=up/dn
        rsi=100-100/(1+rs)
    return round(rsi,2)

def cg_closes(cid):
    url=f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
    r=requests.get(url,params={"vs_currency":VS,"days":2},timeout=15)
    r.raise_for_status()
    return [p[1] for p in r.json()["prices"]]

def grid_suggestion(rsi, closes):
    lo = round(min(closes[-48:]), -1)
    hi = round(max(closes[-48:]), -1)
    grids = 20
    mode  = "Arithmetic"
    trailing = "✅ Enabled"
    direction = "Long" if rsi < RSI_LOW else "Short"
    return lo, hi, grids, mode, trailing, direction

# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    alerts=[]
    for cid,sym in COINS.items():
        try:
            closes=cg_closes(cid)
            rsi=calc_rsi(closes[-15:])
            if rsi < RSI_LOW or rsi > RSI_HIGH:
                lo,hi,grids,mode,trail,dirn = grid_suggestion(rsi, closes)
                status="Oversold" if rsi<RSI_LOW else "Overbought"
                alerts.append(
f"""🔻 *{sym}* RSI {rsi:.2f} — {status}!

📊 *Grid Bot Settings* ({sym.upper()}/{VS.upper()}):
• Price Range: {lo} – {hi}
• Grids: {grids}
• Mode: {mode}
• Trailing: {trail}
• Direction: *{dirn}*"""
                )
        except Exception as e:
            print(f"[WARN] {sym}: {e}", file=sys.stderr)

    if alerts:
        ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        send_tg(f"*RSI Alert Bot* — {ts}\n\n" + "\n\n".join(alerts))

if __name__=="__main__":
    main()
