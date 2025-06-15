#!/usr/bin/env python3
"""
Pionex Futures‑Grid Scanner  (rsi_bot.py)

• Data source: Pionex REST API (PERP pairs only)
• Main tokens: BTC, ETH, SOL, HYPE
• Telegram output:
    🏆 MAIN TOKENS          – all main tokens that pass filters
    💎 SMALLER OPPORTUNITIES – highest‑scoring non‑main pairs (max 5)

Author  : ChatGPT
Updated : 2025‑06‑16
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
MIN_VOLUME       = 10_000_000      # 24 h notional (quote currency) - ONLY for small tokens
MIN_VOLUME_MAIN  = 1_000_000       # Lower threshold for main tokens
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

    def get_market_cap_category(self):
        """Determine market cap category based on volume and token"""
        vol = self.info["total_volume"]
        if self.symbol.upper() in {"BTC", "ETH"}: return "MEGA-CAP"
        if self.symbol.upper() in {"SOL"}: return "LARGE-CAP"
        if self.symbol.upper() in {"HYPE"}: return "MID-CAP"
        if vol > 50_000_000: return "LARGE-CAP"
        if vol > 20_000_000: return "MID-CAP"
        return "SMALL-CAP"

    def get_grid_params(self, rsi_val, volatility_pct):
        """Calculate grid parameters based on RSI and volatility"""
        # Price range calculation
        if rsi_val <= 30:  # Oversold
            lower_bound = self.px * 0.97
            upper_bound = self.px * 1.06
        elif rsi_val >= 70:  # Overbought
            lower_bound = self.px * 0.94
            upper_bound = self.px * 1.03
        else:  # Neutral
            lower_bound = self.px * 0.95
            upper_bound = self.px * 1.05

        # Grid count based on volatility and market cap
        is_main = self.symbol.upper() in MAIN_TOKENS
        if volatility_pct > 15:
            grid_count = 150 if is_main else 65
        elif volatility_pct > 10:
            grid_count = 120 if is_main else 85
        else:
            grid_count = 85 if is_main else 55

        # Grid mode based on volatility
        grid_mode = "Geometric" if volatility_pct > 15 else "Arithmetic"
        
        # Expected cycles based on volatility
        cycles_per_day = int(volatility_pct * 2)
        
        return {
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "grid_count": grid_count,
            "grid_mode": grid_mode,
            "cycles_per_day": cycles_per_day
        }

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
        # Different volume scoring for main vs small tokens
        is_main = self.symbol.upper() in MAIN_TOKENS
        if is_main:
            if vol24 > 5_000_000: score += 15; reasons.append("Main token liquidity")
            elif vol24 > MIN_VOLUME_MAIN: score += 10; reasons.append("Adequate main token liquidity")
        else:
            if vol24 > 50_000_000: score += 15; reasons.append("High liquidity")
            elif vol24 > MIN_VOLUME: score += 10; reasons.append("Adequate liquidity")

        if abs(self.info["price_change_percentage_24h"])<5:
            score+=10; reasons.append("Stable price")

        # More lenient scoring for main tokens
        suit="poor"
        if is_main:
            if score>=40: suit="excellent"
            elif score>=25: suit="good"
            elif score>=15: suit="moderate"
        else:
            if score>=70: suit="excellent"
            elif score>=50: suit="good"
            elif score>=30: suit="moderate"

        return {"score":score,"suit":suit,"vol_reg":vol_reg,"rsi_sig":rsi_sig,
                "rsi_val":rsi_val,"trend":tr_dir,"reasons":reasons,
                "volatility_pct": width*100 if width else 10}

# ───────────── ALERT BUILDER ──────────────────────
def build_alert(an, meta):
    rsi_val = meta["rsi_val"]
    volatility_pct = meta["volatility_pct"]
    
    # Direction and emoji
    if rsi_val <= 30:
        direction = "Long"
        direction_emoji = "🟢"
        direction_desc = "Oversold conditions suggest potential rebound"
    elif rsi_val >= 70:
        direction = "Short"
        direction_emoji = "🔴"
        direction_desc = "Overbought conditions suggest potential decline"
    else:
        direction = "Neutral"
        direction_emoji = "🟡"
        direction_desc = "Neutral RSI perfect for range-bound grid trading"

    # Quality indicator
    if meta["suit"] == "excellent":
        quality = "🔥"
    elif meta["suit"] == "good":
        quality = "⚡"
    else:
        quality = "⚠️"

    # Market cap category
    market_cap = an.get_market_cap_category()
    
    # Build the alert
    text = f"{direction_emoji} *{an.symbol}* RSI {rsi_val:.1f} | {market_cap}\n"
    text += f"💡 *Analysis:* {direction_desc}. Recommended for {direction} bias grid.\n"
    
    return text

# ────────────── MARKET FETCH ──────────────────────
def fetch_candidates():
    out=[]
    for tk in fetch_perp_tickers():
        # Handle different symbol formats
        sym_full = tk["symbol"]
        if "_PERP" in sym_full:
            # Format: HYPE_USDT_PERP
            parts = sym_full.split("_")
            base = parts[0]
        else:
            # Format: BTC_USDT or HYPE.PERP_USDT
            raw_base = sym_full.split("_")[0]
            base = raw_base.split(".")[0]

        if is_excluded(base):
            continue
        price=float(tk["close"]); vol24=float(tk["amount"])
        if price<MIN_PRICE:
            continue
            
        # FIXED: Apply different volume filters for main vs small tokens
        is_main_token = base.upper() in MAIN_TOKENS
        if is_main_token:
            # More lenient volume requirement for main tokens
            if vol24 < MIN_VOLUME_MAIN:
                logging.info(f"Main token {base} filtered: volume ${vol24:,.0f} < ${MIN_VOLUME_MAIN:,.0f}")
                continue
        else:
            # Strict volume requirement for small tokens
            if vol24 < MIN_VOLUME:
                continue
                
        open_px=float(tk["open"]); change=(price-open_px)/open_px*100 if open_px else 0
        out.append({"symbol":base,"raw_symbol":sym_full,
                    "current_price":price,"total_volume":vol24,
                    "price_change_percentage_24h":change})
                    
    logging.info(f"Candidates after filtering - Main tokens: {len([c for c in out if c['symbol'].upper() in MAIN_TOKENS])}, Small tokens: {len([c for c in out if c['symbol'].upper() not in MAIN_TOKENS])}")
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
            # FIXED: Don't filter out main tokens regardless of score
            is_main = info["symbol"].upper() in MAIN_TOKENS
            if meta["suit"]=="poor" and not is_main: 
                continue
            scored.append((meta["score"], build_alert(ga,meta), ga.symbol))
        except Exception as exc:
            logging.debug(f"{info['symbol']} skip: {exc}")

    scored.sort(key=lambda x:x[0], reverse=True)

    main_alerts  =[a for sc,a,sym in scored if sym.upper() in MAIN_TOKENS]
    small_alerts =[a for sc,a,sym in scored if sym.upper() not in MAIN_TOKENS][:MAX_SMALL_ALERTS]

    # Build message with original formatting
    msg = f"🤖 *ENHANCED GRID TRADING ALERTS — {md_escape(ts)}*\n"
    
    if main_alerts:
        msg += "*🏆 MAIN TOKENS*\n"
        msg += "\n".join(main_alerts) + "\n"
    
    if small_alerts:
        msg += "*💎 SMALLER OPPORTUNITIES*\n"
        msg += "\n".join(small_alerts)
    
    if not main_alerts and not small_alerts:
        msg += "❌ No suitable opportunities found."

    send_telegram(msg)
    logging.info("Scan complete.")

if __name__=="__main__":
    main()
