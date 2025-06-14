# rsi_grid_combo.py  ────────────────────────────────────────────────────────────
# • Alerts for BTC / ETH / SOL / HYPE when RSI is extreme
# • Recommends grid settings with *dynamic grid count* based on volatility
# • Suggests 3‑5 additional sideways coins every hour (sparkline scan)

import requests, numpy as np, sys
from datetime import datetime, timezone

# ─── TELEGRAM CREDENTIALS ──────────────────────────────────────────────────────
TOKEN  = "7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8"
CHATID = "7588547693"

def tg(msg:str):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHATID, "text": msg, "parse_mode": "Markdown"},
        timeout=12,
    )

# ─── CONSTANTS ─────────────────────────────────────────────────────────────────
VS = "usd"

FOCUS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "hyperliquid": "HYPE",
}
RSI_LOW, RSI_HIGH = 35, 65

# Scanner parameters
SCAN_TOP_N, SCAN_PICKS = 50, 5
RS_SC_LOW, RS_SC_HIGH = 40, 60
VOL_MIN, VOL_MAX = 0.03, 0.08
TREND_MAX = 0.65
EXCLUDE = set(FOCUS.values())

# ─── TA HELPERS ────────────────────────────────────────────────────────────────
def rsi(vals, period=14):
    vals = np.array(vals)
    delta = np.diff(vals)
    seed = delta[:period]
    up = seed[seed >= 0].sum() / period
    dn = -seed[seed < 0].sum() / period or 1e-9
    rs = up / dn
    r = 100 - 100 / (1 + rs)
    for d in delta[period:]:
        g = max(d, 0)
        l = -min(d, 0)
        up = (up * (period - 1) + g) / period
        dn = (dn * (period - 1) + l) / period or 1e-9
        rs = up / dn
        r = 100 - 100 / (1 + rs)
    return round(r, 2)

def cg_closes(cid):
    url = f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart"
    j = requests.get(url, params={"vs_currency": VS, "days": 2}, timeout=20).json()
    return [p[1] for p in j["prices"]]

def markets_spark():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    j = requests.get(
        url,
        params={
            "vs_currency": VS,
            "order": "volume_desc",
            "per_page": SCAN_TOP_N,
            "page": 1,
            "sparkline": "true",
            "price_change_percentage": "24h",
        },
        timeout=25,
    ).json()
    return j

# ─── GRID PARAMS WITH DYNAMIC GRID COUNT ───────────────────────────────────────
def grid_params(closes):
    lo = round(min(closes[-48:]), 0 if max(closes) < 10 else -1)
    hi = round(max(closes[-48:]), 0 if max(closes) < 10 else -1)
    vol_pct = (hi - lo) / closes[-1]

    if vol_pct > 0.08:
        grids = 28
    elif vol_pct > 0.05:
        grids = 20
    elif vol_pct > 0.03:
        grids = 15
    else:
        grids = 10

    return lo, hi, grids, "Arithmetic", "Disabled"

# ─── BUILD TELEGRAM MESSAGE ────────────────────────────────────────────────────
def build_msg():
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"*HOURLY RSI ALERT — {ts}*")

    # focus‑coin RSI
    for cid, sym in FOCUS.items():
        try:
            closes = cg_closes(cid)
            r = rsi(closes[-15:])
            if r < RSI_LOW or r > RSI_HIGH:
                lo, hi, grids, mode, trail = grid_params(closes)
                state = "Oversold" if r < RSI_LOW else "Overbought"
                direction = "Long" if r < RSI_LOW else "Short"
                emoji = "🔻" if r < RSI_LOW else "🔺"
                lines += [
                    f"\n{emoji} *{sym} RSI {r:.2f}*",
                    f"📊 *{sym} Grid Bot Suggestion*",
                    f"• Price Range: ${lo:,} – ${hi:,}",
                    f"• Grids: {grids}",
                    f"• Mode: {mode}",
                    f"• Trailing: {trail}",
                    f"• Direction: {direction}",
                ]
        except Exception as e:
            print(f"[WARN] {cid}: {e}", file=sys.stderr)

    # sparkline scanner
    picks = []
    try:
        for c in markets_spark():
            sym = c["symbol"].upper()
            if sym in EXCLUDE:
                continue
            closes = c.get("sparkline_in_7d", {}).get("price", [])
            if len(closes) < 48:
                continue
            closes = closes[-48:]
            r = rsi(closes[-15:])
            vol = (max(closes[-24:]) - min(closes[-24:])) / closes[-1]
            trend = np.std(closes[-6:]) / np.std(closes[-24:]) or 0
            if RS_SC_LOW < r < RS_SC_HIGH and VOL_MIN < vol < VOL_MAX and trend < TREND_MAX:
                picks.append((sym, closes))
            if len(picks) >= SCAN_PICKS_MAX:
                break
    except Exception as e:
        print(f"[WARN] scanner: {e}", file=sys.stderr)

    if picks:
        lines.append("\n📊 *Sideways coins to grid now*")
        for sym, closes in picks:
            lo, hi, grids, mode, trail = grid_params(closes)
            lines += [
                f"{sym}",
                f"• Price Range: ${lo:,} – ${hi:,}",
                f"• Grids: {grids}",
                f"• Mode: {mode}",
                f"• Trailing: {trail}",
                f"• Direction: Neutral",
            ]
    else:
        lines.append("\n_No additional sideways coins found._")

    return "\n".join(lines)

# ─── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        tg(build_msg())
    except Exception as e:
        tg(f"❌ combo bot error: {e}")
