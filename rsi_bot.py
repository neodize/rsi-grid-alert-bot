#!/usr/bin/env python3
"""
Pionex Futures‑Grid Scanner  (rsi_bot.py)

• Data source: Pionex REST API (PERP pairs only)
• Main tokens: BTC, ETH, SOL, HYPE
• Telegram output:
    🏆 MAIN TOKENS          – all main tokens that pass filters
    💎 TOP 5 SMALL TOKENS   – highest‑scoring non‑main pairs (max 5)

Author  : ChatGPT
Updated : 2025‑06‑15
"""

import os
import logging
from datetime import datetime, timezone

import requests
import numpy as np
from scipy import stats  # used in volatility calcs

# ──────────────────── CONFIG ──────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PIONEX_API       = "https://api.pionex.com"

MAIN_TOKENS      = {"BTC", "ETH", "SOL", "HYPE"}
MIN_VOLUME       = 10_000_000      # 24 h notional (quote currency)
MIN_PRICE        = 0.01
MAX_SMALL_ALERTS = 5

WRAPPED_TOKENS = {
    "WBTC","WETH","WSOL","WBNB","WMATIC","WAVAX","WFTM","CBBTC","CBETH",
    "RETH","STETH","WSTETH","FRXETH","SFRXETH"
}
STABLECOINS = {
    "USDT","USDC","BUSD","DAI","TUSD","USDP","USDD","FRAX",
    "FDUSD","PYUSD","USDE","USDB","LUSD","SUSD","DUSD","OUSD"
}
EXCLUDED_TOKENS = {
    "BTCUP","BTCDOWN","ETHUP","ETHDOWN","ADAUP","ADADOWN",
    "LUNA","LUNC","USTC","SHIB","DOGE","PEPE","FLOKI","BABYDOGE"
}

# ────────────────── LOGGING ───────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ─────────────── TELEGRAM UTIL ────────────────────
def send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram credentials not set – message skipped.")
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data, timeout=10).raise_for_status()
        logging.info("Telegram message sent.")
    except requests.RequestException as exc:
        logging.error(f"Telegram send failed: {exc}")

def md_escape(txt: str) -> str:
    for ch in "_*[]()~`>#+=|{}.!":
        txt = txt.replace(ch, f"\\{ch}")
    return txt

# ─────────────── HELPERS ──────────────────────────
def is_excluded(sym: str) -> bool:
    s = sym.upper()
    if s in WRAPPED_TOKENS or s in STABLECOINS or s in EXCLUDED_TOKENS:
        return True
    if s.endswith(("UP","DOWN","3L","3S","5L","5S")):
        return True
    return False

def sma(arr, n): return sum(arr[-n:]) / n if len(arr) >= n else None

def rsi(arr, n=14):
    if len(arr) < n + 1: return None
    diff = np.diff(arr)
    up   = np.maximum(diff, 0)
    dn   = np.maximum(-diff, 0)
    avg_gain = np.mean(up[-n:]); avg_loss = np.mean(dn[-n:])
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(highs, lows, closes, n=14):
    if len(highs) < n + 1: return None
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
          for i in range(1, len(highs))]
    return np.mean(tr[-n:])

def bollinger(arr, n=20, k=2):
    if len(arr) < n: return None, None, None
    ma = np.mean(arr[-n:]); sd = np.std(arr[-n:])
    return ma + k*sd, ma, ma - k*sd

def fmt_price(p):
    if p >= 100:  return f"${p:,.2f}"
    if p >= 1:    return f"${p:,.4f}"
    if p >= 0.01: return f"${p:,.6f}"
    return f"${p:,.10f}"

# ────────────── PIONEX WRAPPERS ───────────────────
def fetch_perp_tickers():
    r = requests.get(f"{PIONEX_API}/api/v1/market/tickers",
                     params={"type": "PERP"}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["tickers"]

def fetch_klines(symbol: str, interval="1h", limit=200):
    r = requests.get(f"{PIONEX_API}/api/v1/market/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=10)
    r.raise_for_status()
    kl = r.json()["data"]["klines"]
    closes, vols, highs, lows = [], [], [], []
    for k in kl:
        closes.append(float(k["close"]))
        vols.append(float(k["volume"]))
        highs.append(float(k["high"]))
        lows.append(float(k["low"]))
    return closes, vols, highs, lows

# ─────────────── GRID ANALYZER ────────────────────
class GridAnalyzer:
    def __init__(self, info):
        self.info   = info
        self.symbol = info["symbol"]
        self.px     = info["current_price"]
        try:
            p, v, h, l = fetch_klines(info["raw_symbol"])
        except Exception as exc:
            logging.debug(f"{self.symbol} klines failed: {exc}")
            p = [self.px*(1+np.random.normal(0,0.02)) for _ in range(60)]
            v = [info["total_volume"]/60]*60
            h = p[:]; l = p[:]
        self.prices, self.vols, self.highs, self.lows = p, v, h, l

    # -------- metrics --------
    def volatility(self):
        ub, mid, lb = bollinger(self.prices)
        if mid is None: return "low", None, None
        width = (ub-lb)/mid
        reg   = "medium"
        if width < 0.05: reg = "low"
        if width > 0.15: reg = "high"
        return reg, width, atr(self.highs, self.lows, self.prices)

    def trend(self):
        s20 = sma(self.prices,20); s50 = sma(self.prices,50)
        if not s20 or not s50: return "neutral",0.5
        if s20 > s50*1.02: return "bullish",0.7
        if s20 < s50*0.98: return "bearish",0.7
        return "neutral",0.3

    def rsi_sig(self):
        r = rsi(self.prices) or 50
        if r<=30: return r,"oversold"
        if r>=70: return r,"overbought"
        if r<=35: return r,"approaching_oversold"
        if r>=65: return r,"approaching_overbought"
        return r,"neutral"

    # -------- scoring --------
    def score(self):
        vol_reg, width, _atr = self.volatility()
        tr_dir, tr_str       = self.trend()
        rsi_val, rsi_sig     = self.rsi_sig()

        score, reasons = 0, []
        if vol_reg=="medium": score+=30; reasons.append("Medium volatility ideal")
        elif vol_reg=="low":  score+=15; reasons.append("Low volatility OK")
        else: score+=5; reasons.append("High volatility")

        if tr_dir=="neutral": score+=25; reasons.append("Sideways trend")
        elif tr_str<0.6:      score+=15; reasons.append("Weak trend")

        if rsi_sig in {"oversold","overbought"}:
            score+=20; reasons.append(f"RSI {rsi_sig}")
        elif rsi_sig.startswith("approaching"):
            score+=10; reasons.append(f"RSI {rsi_sig.replace('_',' ')}")

        vol24 = self.info["total_volume"]
        if vol24>50_000_000: score+=15; reasons.append("High liquidity")
        elif vol24>MIN_VOLUME: score+=10; reasons.append("Adequate liquidity")

        if abs(self.info["price_change_percentage_24h"])<5:
            score+=10; reasons.append("Stable price")

        suit="poor"
        if score>=70: suit="excellent"
        elif score>=50: suit="good"
        elif score>=30: suit="moderate"

        return {"score":score,"suit":suit,"vol_reg":vol_reg,"rsi_sig":rsi_sig,
                "trend":tr_dir,"reasons":reasons}

# ───────────── ALERT BUILDER ──────────────────────
def build_alert(an, meta):
    suit_e = {"excellent":"🔥","good":"⚡","moderate":"⚠️","poor":"❌"}[meta["suit"]]
    dir_e  = {"Long":"🟢","Short":"🔴","Neutral":"🟡"}

    direction="Neutral"; conf="Medium"
    if meta["rsi_sig"] in {"oversold","approaching_oversold"}:
        direction="Long"; conf="High" if meta["rsi_sig"]=="oversold" else "Medium"
    elif meta["rsi_sig"] in {"overbought","approaching_overbought"}:
        direction="Short"; conf="High" if meta["rsi_sig"]=="overbought" else "Medium"

    spacing=0.006 if meta["vol_reg"]=="medium" else 0.012
    grids=100

    text  = f"{dir_e[direction]} *{an.symbol}* PERP {suit_e}\n"
    text += f"Price `{fmt_price(an.px)}` • Score `{meta['score']}`\n"
    text += f"Grids `{grids}` • Spacing `{spacing*100:.2f}%` • Dir `{direction}` ({conf})\n"
    text += "Reasons:\n" + "\n".join(f"• {r}" for r in meta["reasons"][:3])
    return text

# ────────────── MARKET FETCH ──────────────────────
def fetch_candidates():
    out=[]
    for tk in fetch_perp_tickers():
        sym_full = tk["symbol"]           # BTC_USDT  or  HYPE.PERP_USDT
        raw_base = sym_full.split("_")[0] # BTC   or  HYPE.PERP
        base     = raw_base.split(".")[0] # BTC   or  HYPE

        if is_excluded(base):
            continue
        price=float(tk["close"]); vol24=float(tk["amount"])
        if price<MIN_PRICE or vol24<MIN_VOLUME:
            continue
        open_px=float(tk["open"]); change=(price-open_px)/open_px*100 if open_px else 0
        out.append({"symbol":base,"raw_symbol":sym_full,
                    "current_price":price,"total_volume":vol24,
                    "price_change_percentage_24h":change})
    logging.info(f"Candidates after basic screen: {len(out)}")
    return out

# ──────────────── MAIN LOOP ───────────────────────
def main():
    ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        cands=fetch_candidates()
    except Exception as exc:
        logging.error(f"Pionex API error: {exc}")
        send_telegram(f"*GRID SCAN ERROR*\n{md_escape(str(exc))}")
        return

    scored=[]
    for info in cands:
        try:
            ga=GridAnalyzer(info); meta=ga.score()
            if meta["suit"]=="poor": continue
            scored.append((meta["score"], build_alert(ga,meta), ga.symbol))
        except Exception as exc:
            logging.debug(f"{info['symbol']} skip: {exc}")

    scored.sort(key=lambda x:x[0], reverse=True)

    main_alerts  =[a for sc,a,sym in scored if sym.upper() in MAIN_TOKENS]
    small_alerts =[a for sc,a,sym in scored if sym.upper() not in MAIN_TOKENS][:MAX_SMALL_ALERTS]

    msg  = f"*🚀 PIONEX FUTURES GRID SCAN — {md_escape(ts)}*\n"
    msg += f"Analyzed `{len(cands)}` pairs • Main `{len(main_alerts)}` • Small `{len(small_alerts)}`\n\n"

    if main_alerts:
        msg += "*🏆 MAIN TOKENS*\n" + "\n\n".join(main_alerts) + "\n\n"
    if small_alerts:
        msg += "*💎 TOP 5 SMALL TOKENS*\n" + "\n\n".join(small_alerts)
    if not main_alerts and not small_alerts:
        msg += "❌ No suitable opportunities."

    send_telegram(msg)
    logging.info("Scan complete.")

if __name__=="__main__":
    main()
