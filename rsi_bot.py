# rsi_grid_combo.py  ─ one message/hour: focus‑coin RSI + sideways scanner
import requests, numpy as np, sys
from datetime import datetime, timezone

TOKEN  = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHATID = "7588547693"
def tg(m): requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id":CHATID,"text":m,"parse_mode":"Markdown"},timeout=12)

VS="usd"
FOCUS={"bitcoin":"BTC","ethereum":"ETH","solana":"SOL","hyperliquid":"HYPE"}
RSI_L,RSI_H=35,65
SCAN_TOP,SCAN_MAX=50,5
RS_L,RS_H=40,60
VOL_MIN,VOL_MAX=0.03,0.08
TREND_MAX=0.65
EXCLUDE=set(FOCUS.values())

def rsi(v,p=14):
    v=np.array(v); d=np.diff(v)
    up=d.clip(min=0); dn=-d.clip(max=0)
    avg_up=up[:p].mean(); avg_dn=dn[:p].mean() or 1e-9
    rs=avg_up/avg_dn; r=100-100/(1+rs)
    for g,l in zip(up[p:],dn[p:]):
        avg_up=(avg_up*(p-1)+g)/p
        avg_dn=(avg_dn*(p-1)+l)/p or 1e-9
        rs=avg_up/avg_dn; r=100-100/(1+rs)
    return round(r,2)

def cg_closes(cid):
    url=f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
    return [p[1] for p in requests.get(url,params={"vs_currency":VS,"days":2},
             timeout=20).json()["prices"]]

def markets_spark():
    url="https://api.coingecko.com/api/v3/coins/markets"
    return requests.get(url,params={
        "vs_currency":VS,"order":"volume_desc","per_page":SCAN_TOP,
        "page":1,"sparkline":"true"},timeout=25).json()

# smart price format
def fmt(p):
    if p>=1:   return f"${p:,.2f}"
    if p>=0.01:return f"${p:,.4f}"
    return f"${p:,.8f}"

def grid_params(closes):
    lo=min(closes[-48:]); hi=max(closes[-48:])
    if hi==lo or (hi-lo)/lo<0.005:
        pad=lo*0.01; lo-=pad; hi+=pad
    rng_pct=(hi-lo)/((hi+lo)/2)
    grids=28 if rng_pct>0.08 else 20 if rng_pct>0.05 else 15 if rng_pct>0.03 else 10
    return fmt(lo),fmt(hi),grids,"Arithmetic","Disabled"

def build():
    ts=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    out=[f"*HOURLY RSI ALERT — {ts}*"]

    # focus coins
    for cid,sym in FOCUS.items():
        try:
            closes=cg_closes(cid); r=rsi(closes[-15:])
            if r<RSI_L or r>RSI_H:
                lo,hi,g,mode,trail=grid_params(closes)
                dirn="Long" if r<RSI_L else "Short"
                emoji="🔻" if r<RSI_L else "🔺"
                price=fmt(closes[-1])
                out+=[
                    f"\n{emoji} *{sym} {price} RSI {r:.2f}*",
                    f"📊 *{sym} Grid Bot Suggestion*",
                    f"• Price Range: {lo} – {hi}",
                    f"• Grids: {g}",
                    f"• Mode: {mode}",
                    f"• Trailing: {trail}",
                    f"• Direction: {dirn}",
                ]
        except Exception as e:
            print(f"[WARN] {cid}: {e}",file=sys.stderr)

    # scanner
    picks=[]
    for c in markets_spark():
        sym=c["symbol"].upper()
        if sym in EXCLUDE: continue
        prices=c.get("sparkline_in_7d",{}).get("price",[])
        if len(prices)<48: continue
        closes=prices[-48:]
        r=rsi(closes[-15:]); vol=(max(closes[-24:])-min(closes[-24:]))/closes[-1]
        trend=np.std(closes[-6:])/np.std(closes[-24:]) or 0
        if RS_L<r<RS_H and VOL_MIN<vol<VOL_MAX and trend<TREND_MAX:
            picks.append((sym,closes))
        if len(picks)>=SCAN_MAX: break

    if picks:
        out.append("\n📊 *Sideways coins to grid now*")
        for sym,cl in picks:
            lo,hi,g,mode,trail=grid_params(cl)
            price=fmt(cl[-1])
            out+=[
                f"{sym} {price}",
                f"• Price Range: {lo} – {hi}",
                f"• Grids: {g}",
                f"• Mode: {mode}",
                f"• Trailing: {trail}",
                f"• Direction: Neutral",
            ]
    else:
        out.append("\n_No additional sideways coins found._")

    return "\n".join(out)

if __name__=="__main__":
    try: tg(build())
    except Exception as e: tg(f"❌ bot error: {e}")
