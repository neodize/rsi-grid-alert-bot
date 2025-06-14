import requests, numpy as np, math, statistics
from datetime import datetime

# ─── USER CONFIG ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHAT_ID        = "7588547693"

COINS = {                 # CoinGecko IDs → symbols for the alert
    "bitcoin"      : "BTC",
    "ethereum"     : "ETH",
    "solana"       : "SOL",
    "hyperliquid"  : "HYPE",      # “hyperliquid” is HYPE on CG
}

VS_CURRENCY = "usd"       # CoinGecko quote currency
RSI_PERIOD  = 14
RSI_LOW     = 35          # Oversold  → Long bias
RSI_HIGH    = 70          # Overbought→ Short bias

DAYS_OF_DATA = 2          # 48 hourly candles (free CG plan)

# ─── TELEGRAM ───────────────────────────────────────────────────────────────────
def send_tg(msg:str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    requests.post(url, data=payload, timeout=15)

# ─── DATA HELPERS ───────────────────────────────────────────────────────────────
def cg_closes(coin_id:str):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    r = requests.get(url, params={"vs_currency": VS_CURRENCY, "days": str(DAYS_OF_DATA)}, timeout=20)
    r.raise_for_status()
    closes = [float(p[1]) for p in r.json().get("prices", [])]
    if len(closes) < RSI_PERIOD+1: raise ValueError("Not enough data")
    return closes

def rsi(values, period=RSI_PERIOD):
    v = np.array(values)
    deltas = np.diff(v)
    seed   = deltas[:period]
    up     = seed[seed>0].sum()/period
    down   = -seed[seed<0].sum()/period
    if down == 0: return 100
    rs  = up/down
    rsi = 100 - (100/(1+rs))
    for d in deltas[period:]:
        gain = max(d,0); loss = -min(d,0)
        up   = (up*(period-1)+gain)/period
        down = (down*(period-1)+loss)/period
        rs   = up/down if down else 0
        rsi  = 100 - (100/(1+rs))
    return round(rsi,2)

def ema(values, length=24):
    k=2/(length+1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = v*k + ema_val*(1-k)
    return ema_val

# ─── GRID SUGGESTION LOGIC ──────────────────────────────────────────────────────
def grid_suggestion(prices:list, symbol:str, rsi_val:float):
    # Trend filter: 24‑hour EMA vs last close
    trend_up = prices[-1] > ema(prices[-24:])
    recent_high = max(prices[-24:])
    recent_low  = min(prices[-24:])
    mid         = (recent_high+recent_low)/2
    pct_band    = 0.06 if trend_up else 0.04
    low_price   = round(mid*(1-pct_band), 2)
    high_price  = round(mid*(1+pct_band), 2)

    # Grid qty based on recent volatility
    vol_pct = (recent_high-recent_low)/mid
    grids   = 25 if vol_pct>0.08 else 15

    mode     = "Geometric" if trend_up else "Arithmetic"
    trailing = "Enabled"   if trend_up else "Disabled"

    if rsi_val < RSI_LOW:  direction="Long"
    elif rsi_val > RSI_HIGH: direction="Short"
    else: direction="Neutral"

    return (
        f"📊 *{symbol} Grid Bot Suggestion*\n"
        f"• Price Range: `${low_price}` – `${high_price}`\n"
        f"• Grids: {grids}\n"
        f"• Mode: {mode}\n"
        f"• Trailing: {trailing}\n"
        f"• Direction: *{direction}*"
    )

# ─── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    alerts=[]
    for cg_id,sym in COINS.items():
        try:
            closes = cg_closes(cg_id)
            last_rsi = rsi(closes)
            if last_rsi < RSI_LOW or last_rsi > RSI_HIGH:
                emoji = "🔻" if last_rsi<RSI_LOW else "🚀"
                header = f"{emoji} *{sym}* RSI {last_rsi:.2f}"
                alerts.append(header)
                alerts.append(grid_suggestion(closes, sym, last_rsi))
        except Exception as e:
            alerts.append(f"❌ *{sym}* error: {e}")

    if alerts:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        send_tg(f"*HOURLY RSI ALERT* — {ts}\n\n" + "\n".join(alerts))
    # else:  # uncomment if you want “no alert” pings
    #     send_tg("✅ No RSI alerts this hour.")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        send_tg(f"❌ Fatal error in RSI Bot: {e}")
