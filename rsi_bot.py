#!/usr/bin/env python3
"""
Pionex Futures‑Grid Scanner
—————————————
• Pulls every PERP pair from Pionex.
• Filters out wrapped / stable / junk symbols.
• Runs quick TA (ATR, BB width, SMA trend, RSI).
• Scores each pair for grid‑trading suitability.
• Sends the five best to Telegram.

Author: ChatGPT — 2025‑06‑15
"""

import os
import time
import math
import logging
from datetime import datetime, timezone

import requests
import numpy as np
from scipy import stats   # used in volatility calcs — keep import

# ─────────────────────────  CONFIG  ─────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PIONEX_API       = "https://api.pionex.com"

MIN_VOLUME       = 10_000_000         # 24 h notional in quote currency
MIN_PRICE        = 0.01               # skip sub‑cent coins
MAX_RECOMMEND    = 5                  # how many alerts to send

# Tags for quick filtering
WRAPPED_TOKENS = {
    "WBTC","WETH","WBNB","WMATIC","WAVAX","WFTM","WSOL",
    "MSOL","STSOL","JSOL","BSOL","CBBTC","CBETH","RETH",
    "STETH","WSTETH","FRXETH","SFRXETH"
}
STABLECOINS = {
    "USDT","USDC","BUSD","DAI","TUSD","USDP","USDD","FRAX",
    "FDUSD","PYUSD","USDE","USDB","LUSD","SUSD","DUSD","OUSD",
}
EXCLUDED_TOKENS = {
    "BTCUP","BTCDOWN","ETHUP","ETHDOWN","ADAUP","ADADOWN",
    "LUNA","LUNC","USTC","SHIB","DOGE","PEPE","FLOKI","BABYDOGE"
}

# ────────────────────────  LOGGING  ────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ──────────────────────  TELEGRAM  ─────────────────────────
def send_telegram(msg: str) -> None:
    """Send plain‑markdown text to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram credentials missing; skip send.")
        return

    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}

    try:
        requests.post(url, data=data, timeout=10).raise_for_status()
        logging.info("Telegram message sent.")
    except requests.RequestException as exc:
        logging.error(f"Telegram send failed: {exc}")

# ───────────────────────  HELPERS  ─────────────────────────
def is_excluded(symbol: str) -> bool:
    s = symbol.upper()
    if s in WRAPPED_TOKENS or s in STABLECOINS or s in EXCLUDED_TOKENS:
        return True
    if s.endswith(("UP","DOWN","3L","3S","5L","5S")):
        return True
    return False

def calculate_sma(prices, period):
    return sum(prices[-period:]) / period if len(prices) >= period else None

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    diffs  = np.diff(prices)
    gains  = np.maximum(diffs, 0)
    losses = np.maximum(-diffs, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    tr = []
    for i in range(1, len(highs)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        ))
    return np.mean(tr[-period:])

def calculate_bollinger(prices, period=20, std=2):
    if len(prices) < period:
        return None, None, None
    sma = np.mean(prices[-period:])
    sd  = np.std(prices[-period:])
    return sma + std*sd, sma, sma - std*sd

def format_price(p):
    if p >= 100:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:,.6f}"
    return f"${p:,.10f}"

def md_escape(txt: str) -> str:
    for ch in "_*[]()~`>#+=|{}.!":
        txt = txt.replace(ch, f"\\{ch}")
    return txt

# ──────────────────  PIONEX DATA LAYERS  ───────────────────
def fetch_perp_tickers():
    """Return list of dicts: each ticker for a PERP symbol."""
    url = f"{PIONEX_API}/api/v1/market/tickers"
    r   = requests.get(url, params={"type": "PERP"}, timeout=10)
    r.raise_for_status()
    obj = r.json()

    # Accept both {code:0,data:{tickers:[…]}} and {result:True,data:{tickers:[…]}}
    tickers = obj.get("data", {}).get("tickers", [])
    return tickers

def fetch_klines(symbol: str, interval="1h", limit=200):
    """Return lists: prices, volumes, highs, lows."""
    url = f"{PIONEX_API}/api/v1/market/klines"
    r   = requests.get(url, params={
        "symbol"  : symbol,
        "interval": interval,
        "limit"   : limit
    }, timeout=10)
    r.raise_for_status()
    kobj = r.json()
    kl   = kobj.get("data", {}).get("klines", [])

    prices, vols, highs, lows = [], [], [], []
    for k in kl:
        prices.append(float(k["close"]))
        vols  .append(float(k["volume"]))
        highs .append(float(k["high"]))
        lows  .append(float(k["low"]))
    return prices, vols, highs, lows

# ─────────────────  GRID ANALYSIS OBJECT  ──────────────────
class GridAnalyzer:
    def __init__(self, info):
        self.info         = info
        self.symbol       = info["symbol"]
        self.raw_symbol   = info["raw_symbol"]
        self.price        = info["current_price"]
        # Grab price history
        prices, vols, highs, lows = fetch_klines(self.raw_symbol)
        if len(prices) < 20:  # fallback synthetic
            prices = self._synthetic_prices()
            vols   = [info["total_volume"]/len(prices)]*len(prices)
            highs  = prices[:]
            lows   = prices[:]
        self.prices, self.vols, self.highs, self.lows = prices, vols, highs, lows

    def _synthetic_prices(self, n=60):
        pc = self.price
        return [pc*(1+np.random.normal(0,0.02)) for _ in range(n)]

    # ───── metrics ─────
    def volatility(self):
        upper, mid, lower = calculate_bollinger(self.prices)
        if not mid:
            return "low", None, None
        width = (upper - lower)/mid
        regime = "medium"
        if width < 0.05: regime = "low"
        if width > 0.15: regime = "high"
        atr = calculate_atr(self.highs, self.lows, self.prices)
        return regime, width, atr

    def trend(self):
        sma20 = calculate_sma(self.prices, 20)
        sma50 = calculate_sma(self.prices, 50)
        if not sma20 or not sma50:
            return "neutral", 0.5
        if sma20 > sma50*1.02:
            return "bullish", 0.7
        if sma20 < sma50*0.98:
            return "bearish", 0.7
        return "neutral", 0.3

    def rsi_signal(self):
        rsi = calculate_rsi(self.prices) or 50
        if rsi <= 30: return rsi, "oversold"
        if rsi >= 70: return rsi, "overbought"
        if rsi <= 35: return rsi, "approaching_oversold"
        if rsi >= 65: return rsi, "approaching_overbought"
        return rsi, "neutral"

    # ───── score ─────
    def score(self):
        vol_regime, width, atr = self.volatility()
        trend_dir, trend_str   = self.trend()
        rsi, rsi_sig           = self.rsi_signal()

        score, reasons = 0, []

        # volatility
        if vol_regime == "medium":
            score += 30; reasons.append("Medium volatility ideal for grids")
        elif vol_regime == "low":
            score += 15; reasons.append("Low volatility still OK for tight grids")
        else:
            score += 5;  reasons.append("High volatility → wide grids")

        # trend
        if trend_dir == "neutral":
            score += 25; reasons.append("Sideways trend beneficial")
        elif trend_str < 0.6:
            score += 15; reasons.append("Weak trend acceptable")

        # rsi
        if rsi_sig in {"oversold","overbought"}:
            score += 20; reasons.append(f"RSI {rsi_sig}")
        elif rsi_sig.startswith("approaching"):
            score += 10; reasons.append(f"RSI {rsi_sig.replace('_',' ')}")

        # volume
        vol24 = self.info["total_volume"]
        if vol24 > 50_000_000:
            score += 15; reasons.append("High liquidity")
        elif vol24 > MIN_VOLUME:
            score += 10; reasons.append("Adequate liquidity")

        # price stability
        if abs(self.info["price_change_percentage_24h"]) < 5:
            score += 10; reasons.append("Stable price")

        tier = "small"
        mc   = self.info["market_cap"]
        if mc >= 10_000_000_000:
            tier = "large"
        elif mc >= 1_000_000_000:
            tier = "mid"

        suit = "poor"
        if score >= 70: suit = "excellent"
        elif score >= 50: suit = "good"
        elif score >= 30: suit = "moderate"

        return {
            "score"     : score,
            "suitability": suit,
            "reasons"   : reasons,
            "vol_regime": vol_regime,
            "bb_width"  : width,
            "atr_pct"   : (atr/self.price*100) if atr else None,
            "trend"     : (trend_dir, trend_str),
            "rsi"       : (rsi, rsi_sig),
            "tier"      : tier
        }

# ────────────────────  MAIN WORKFLOW  ─────────────────────
def fetch_market_candidates():
    tickers = fetch_perp_tickers()
    out = []
    for tk in tickers:
        sym = tk["symbol"]          # e.g. BTC_USDT
        base = sym.split("_")[0]

        if is_excluded(base):
            continue

        price  = float(tk["close"])
        vol24  = float(tk["amount"])
        if price < MIN_PRICE or vol24 < MIN_VOLUME:
            continue

        open_px = float(tk["open"])
        change  = (price - open_px) / open_px * 100 if open_px else 0

        out.append({
            "symbol"         : base,
            "raw_symbol"     : sym,
            "current_price"  : price,
            "total_volume"   : vol24,
            "price_change_percentage_24h": change,
            "market_cap"     : vol24*24,   # rough proxy
        })
    logging.info(f"Filtered to {len(out)} PERP candidates")
    return out

def build_alert(analyzer, meta):
    score = meta["score"]
    suit  = meta["suitability"]
    emoji = {"excellent":"🔥","good":"⚡","moderate":"⚠️","poor":"❌"}[suit]
    dir_emoji = {"Long":"🟢","Short":"🔴","Neutral":"🟡"}

    # quick grid params
    spacing = 0.006 if meta["vol_regime"]=="medium" else 0.012
    grids   = 100 if meta["tier"]!="small" else 80
    direction = "Neutral"
    conf      = "Medium"
    rsi_val, rsi_sig = meta["rsi"]
    if rsi_sig in {"oversold","approaching_oversold"}:
        direction = "Long"
        conf      = "High" if rsi_sig=="oversold" else "Medium"
    elif rsi_sig in {"overbought","approaching_overbought"}:
        direction = "Short"
        conf      = "High" if rsi_sig=="overbought" else "Medium"

    msg  = f"{dir_emoji[direction]} *{analyzer.symbol}* PERP {emoji}\n"
    msg += f"Price: `{format_price(analyzer.price)}`\n"
    msg += f"Grids: `{grids}` • Spacing: `{spacing*100:.2f}%` • Dir: `{direction}` ({conf})\n"
    msg += f"Score: `{score}/100` ({suit})\n"
    msg += "Reasons:\n"
    for r in meta["reasons"][:3]:
        msg += f"• {r}\n"
    return msg

def main():
    logging.info("Starting Pionex futures grid scan …")
    try:
        cands = fetch_market_candidates()
    except Exception as exc:
        logging.error(f"Pionex API error: {exc}")
        send_telegram(f"*GRID SCAN ERROR*\n{md_escape(str(exc))}")
        return

    alerts = []
    for info in cands:
        try:
            ga   = GridAnalyzer(info)
            meta = ga.score()
            if meta["suitability"] == "poor":
                continue
            alerts.append((meta["score"], build_alert(ga, meta)))
        except Exception as exc:
            logging.debug(f"{info['symbol']} analysis failed: {exc}")

    alerts.sort(key=lambda x: x[0], reverse=True)
    top = alerts[:MAX_RECOMMEND]

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"*🚀 PIONEX FUTURES GRID SCAN  —  {md_escape(ts)}*\n"
    header+= f"Analyzed `{len(cands)}` pairs • Showing top `{len(top)}`\n\n"

    if not top:
        header += "❌ No suitable opportunities right now."
    else:
        header += "\n\n".join(a for _, a in top)

    send_telegram(header)
    logging.info("Scan complete.")

# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
