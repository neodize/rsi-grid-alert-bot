# rsi_grid_combo.py  ────────────────────────────────────────────────────────────
import requests, numpy as np, sys
from datetime import datetime, timezone

# ─── TELEGRAM CREDENTIALS (hard‑coded) ─────────────────────────────────────────
TOKEN  = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHATID = "7588547693"

def tg(msg):  # send Telegram
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHATID, "text": msg,
                            "parse_mode": "Markdown"},
                      timeout=12).raise_for_status()
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}", file=sys.stderr)

# ─── CONSTANTS ─────────────────────────────────────────────────────────────────
VS="usd"
FOCUS = { "bitcoin":"BTC", "ethereum":"ETH",
          "solana":"SOL", "hyperliquid":"HYPE" }
RSI_LOW, RSI_HIGH = 35, 65          # focus‑coin extremes

# scanner
SCAN_TOP_N     = 50
SCAN_PICKS_MAX = 5
RS_SC_LOW, RS_SC_HIGH = 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08
TREND_MAX = 0.65
EXCLUDE_SYM = set(FOCUS.values())

# ─── TECHNICAL HELPER FUNCTIONS ───────────────────────────────────────────────
def rsi(vals, period=14):
    v=np.array(vals); d=np.diff(v)
    seed=d[:period]
    up=seed[seed>=0].sum()/period
    dn=-seed[seed<0].sum()/period or 1e-9
    rs=up/dn; r=100-100/(1+rs)
    for delta in d[period:]:
        g=max(delta,0); l=-min(delta,0)
        up=(up*(period-1)+g)/period
        dn=(dn*(period-1)+l)/period or 1e-9
        rs=up/dn; r=100-100/(1+rs)
    return round(r,2)

def cg_closes(cid):
    url=f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
    j=requests.get(url,params={"vs_currency":VS,"days":2},timeout=20).json()
    return [p[1] for p in j["prices"]]

def markets_spark():
    url="https://api.coingecko.com/api/v3/coins/markets"
    j=requests.get(url,params={
        "vs_currency":VS,"order":"volume_desc","per_page":SCAN_TOP_N,
        "page":1,"sparkline":"true","price_change_percentage":"24h"
    },timeout=25).json()
    return j

def grid_params(closes):
    lo=round(min(closes[-48:]),  0 if max(closes)<10 else -1)
    hi=round(max(closes[-48:]),  0 if max(closes)<10 else -1)
    grids=15
    mode="Arithmetic"
    trailing="Disabled"
    return lo,hi,grids,mode,trailing

# ─── BUILD TELEGRAM MESSAGE ───────────────────────────────────────────────────
def build():
    lines=[]
    ts=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines.append(f"*HOURLY RSI ALERT — {ts}*")

    # --- focus coins
    for cid,sym in FOCUS.items():
        try:
            closes=cg_closes(cid)
            r=rsi(closes[-15:])
            if r<RSI_LOW or r>RSI_HIGH:
                lo,hi,grids,mode,trail = grid_params(closes)
                direction="Long" if r<RSI_LOW else "Short"
                emoji="🔻" if r<RSI_LOW else "🔺"
                lines.append(f"\n{emoji} *{sym} RSI {r:.2f}*")
                lines.append(f"📊 *{sym} Grid Bot Suggestion*")
                lines.append(f"• Price Range: ${lo:,} – ${hi:,}")
                lines.append(f"• Grids: {grids}")
                lines.append(f"• Mode: {mode}")
                lines.append(f"• Trailing: {trail}")
                lines.append(f"• Direction: {direction}")
        except Exception as e:
            print(f"[WARN] focus {cid}: {e}", file=sys.stderr)

    # --- sparkline scanner
    picks=[]
    try:
        for coin in markets_spark():
            sym=coin["symbol"].upper()
            if sym in EXCLUDE_SYM: continue
            prices=coin.get("sparkline_in_7d",{}).get("price",[])
            if len(prices)<48: continue
            closes=prices[-48:]
            r   = rsi(closes[-15:])
            vol = (max(closes[-24:])-min(closes[-24:]))/closes[-1]
            trend=np.std(closes[-6:])/np.std(closes[-24:]) or 0
            if RS_SC_LOW<r<RS_SC_HIGH and VOL_MIN<vol<VOL_MAX and trend<TREND_MAX:
                picks.append((sym,r,vol,trend,closes))
            if len(picks)>=SCAN_PICKS_MAX:
                break
    except Exception as e:
        print(f"[WARN] sparkline: {e}", file=sys.stderr)

    if picks:
        lines.append("\n📊 *Sideways coins to grid now*")
        for sym,r,vol,trend,closes in picks:
            lo,hi,grids,mode,trail=grid_params(closes)
            lines.append(f"{sym}")
            lines.append(f"• Price Range: ${lo:,} – ${hi:,}")
            lines.append(f"• Grids: {grids}")
            lines.append(f"• Mode: {mode}")
            lines.append(f"• Trailing: {trail}")
            lines.append(f"• Direction: Neutral")
    else:
        lines.append("\n_No additional sideways coins found._")

    return "\n".join(lines)

# ─── MAIN ──────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    try:
        tg(build())
    except Exception as e:
        tg(f"❌ combo bot error: {e}")
