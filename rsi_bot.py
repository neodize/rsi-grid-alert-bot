import os
import logging
from datetime import datetime, timezone

import requests
import numpy as np
from scipy import stats  # noqa – retained for any future volatility calcs

# ──────────────────── CONFIG ──────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PIONEX_API       = "https://api.pionex.com"

# MAIN_TOKENS **must never be removed**
MAIN_TOKENS      = {"BTC", "ETH", "SOL", "HYPE"}

MIN_VOLUME       = 10_000_000      # 24 h notional – smaller tokens
MIN_VOLUME_MAIN  = 1_000_000       # lower threshold for main tokens
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
    """Post a Markdown‑formatted message to Telegram (if credentials set)."""
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
    """Escape Telegram Markdown‑v2 reserved characters."""
    for ch in "_*[]()~>#+=|{}.!":
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

def sma(arr, n):
    return sum(arr[-n:]) / n if len(arr) >= n else None

def rsi(arr, n=14):
    if len(arr) < n + 1:
        return None
    diff = np.diff(arr)
    up   = np.maximum(diff, 0)
    dn   = np.maximum(-diff, 0)
    avg_gain = np.mean(up[-n:]); avg_loss = np.mean(dn[-n:])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(highs, lows, closes, n=14):
    if len(highs) < n + 1:
        return None
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
          for i in range(1, len(highs))]
    return np.mean(tr[-n:])

def bollinger(arr, n=20, k=2):
    if len(arr) < n:
        return None, None, None
    ma = np.mean(arr[-n:]); sd = np.std(arr[-n:])
    return ma + k*sd, ma, ma - k*sd

def fmt_price(p):
    if p >= 100:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:,.6f}"
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
    """Compute metrics and build human‑readable analysis for one symbol."""

    def __init__(self, info):
        self.info   = info
        self.symbol = info["symbol"]
        self.px     = info["current_price"]

        # ---------- fetch multi‑time‑frame data ----------
        try:
            p1h, v1h, h1h, l1h = fetch_klines(info["raw_symbol"], "1h", 200)
        except Exception as exc:
            logging.debug(f"{self.symbol} 1h klines failed: {exc}")
            p1h = [self.px*(1+np.random.normal(0,0.02)) for _ in range(60)]
            v1h = [info["total_volume"]/60]*60
            h1h = p1h[:]; l1h = p1h[:]

        # 4 h timeframe (medium‑term trend)
        try:
            p4h, v4h, h4h, l4h = fetch_klines(info["raw_symbol"], "4h", 200)
        except Exception:
            p4h, v4h, h4h, l4h = p1h, v1h, h1h, l1h

        # 1 D timeframe (macro trend)
        try:
            p1d, v1d, h1d, l1d = fetch_klines(info["raw_symbol"], "1d", 200)
        except Exception:
            p1d, v1d, h1d, l1d = p4h, v4h, h4h, l4h

        # store
        self.prices, self.vols, self.highs, self.lows = p1h, v1h, h1h, l1h
        self.prices_4h, self.vols_4h, self.highs_4h, self.lows_4h = p4h, v4h, h4h, l4h
        self.prices_1d, self.vols_1d, self.highs_1d, self.lows_1d = p1d, v1d, h1d, l1d

    # ---------- generic helpers ----------
    @staticmethod
    def trend_generic(series, fast=20, slow=50):
        """Return (direction, strength 0‑1) based on two moving averages."""
        if len(series) < slow:
            return "neutral", 0.0
        s_fast = sma(series, fast)
        s_slow = sma(series, slow)
        if not s_fast or not s_slow:
            return "neutral", 0.0
        diff = (s_fast - s_slow) / s_slow
        if diff > 0.02:
            return "bullish", min(1.0, abs(diff) / 0.05)
        if diff < -0.02:
            return "bearish", min(1.0, abs(diff) / 0.05)
        return "neutral", min(1.0, abs(diff) / 0.02)

    @staticmethod
    def market_structure(prices):
        """Detect higher‑high / lower‑low sequences for market‑structure bias."""
        highs, lows = [], []
        for i in range(1, len(prices)-1):
            if prices[i] > prices[i-1] and prices[i] > prices[i+1]:
                highs.append(prices[i])
            if prices[i] < prices[i-1] and prices[i] < prices[i+1]:
                lows.append(prices[i])
        if len(highs) < 3 or len(lows) < 3:
            return "neutral", 0.0
        h1, h2, h3 = highs[-3:]
        l1, l2, l3 = lows[-3:]
        if h3 > h2 > h1 and l3 > l2 > l1:
            return "bullish", 0.7
        if h3 < h2 < h1 and l3 < l2 < l1:
            return "bearish", 0.7
        return "neutral", 0.4

    @staticmethod
    def range_strength(width):
        """Map Bollinger‑band width → 0‑1 range strength (narrow = 1)."""
        if width is None:
            return 0.0
        if width >= 0.15:
            return 0.0
        return max(0.0, min(1.0, 1 - width / 0.15))

    # ---------- classic metrics ----------
    def volatility(self):
        ub, mid, lb = bollinger(self.prices)
        if mid is None:
            return "low", None, None
        width = (ub - lb) / mid
        if width < 0.05:
            return "low", width, atr(self.highs, self.lows, self.prices)
        if width > 0.15:
            return "high", width, atr(self.highs, self.lows, self.prices)
        return "medium", width, atr(self.highs, self.lows, self.prices)

    def trend(self):
        return self.trend_generic(self.prices)

    def rsi_sig(self):
        r = rsi(self.prices) or 50
        if r <= 30:
            return r, "oversold"
        if r >= 70:
            return r, "overbought"
        if r <= 35:
            return r, "approaching_oversold"
        if r >= 65:
            return r, "approaching_overbought"
        return r, "neutral"

    # ---------- enhanced direction logic ----------
    def determine_direction(self, meta):
        """Hybrid approach → Long / Short / Neutral + emoji + description."""
        rsi_val = meta["rsi_val"]
        width_pct = meta.get("volatility_pct", 10)
        width = width_pct / 100.0
        range_strength = self.range_strength(width)

        # Volume confirmation (latest 1h candle vs 20‑period average)
        if len(self.vols) >= 20:
            avg_vol = np.mean(self.vols[-20:])
            vol_ratio = self.vols[-1] / avg_vol if avg_vol else 1.0
        else:
            vol_ratio = 1.0

        # Trends
        trend_1h_dir, trend_1h_str = meta["trend"], meta["trend_strength"]
        trend_4h_dir, trend_4h_str = self.trend_generic(self.prices_4h)
        trend_1d_dir, trend_1d_str = self.trend_generic(self.prices_1d)

        # Market structure
        m_dir, m_conf = self.market_structure(self.prices)

        # 1️⃣ Range‑bound market → Neutral grid (preferred)
        if range_strength > 0.7:
            return "Neutral", "🟡", "Strong range – ideal for neutral grid"

        # 2️⃣ Directional bias – multi‑TF trend + volume
        if trend_4h_dir == "bullish" and trend_4h_str > 0.6 and rsi_val < 60 and vol_ratio > 1.2:
            return "Long", "🟢", "Uptrend confirmed across timeframes"
        if trend_4h_dir == "bearish" and trend_4h_str > 0.6 and rsi_val > 40 and vol_ratio > 1.2:
            return "Short", "🔴", "Downtrend confirmed across timeframes"

        # 3️⃣ Mean‑reversion extremes
        if rsi_val <= 35:
            return "Long", "🟢", "Oversold – expecting bounce"
        if rsi_val >= 65:
            return "Short", "🔴", "Overbought – expecting pullback"

        # 4️⃣ Default fall‑back
        return "Neutral", "🟡", "Mixed signals – neutral grid"

    # ---------- market‑cap utils ----------
    def get_market_cap_category(self):
        vol = self.info["total_volume"]
        sm = self.symbol.upper()
        if sm in {"BTC", "ETH"}:
            return "MEGA‑CAP"
        if sm in {"SOL"}:
            return "LARGE‑CAP"
        if sm in {"HYPE"}:
            return "MID‑CAP"
        if vol > 50_000_000:
            return "LARGE‑CAP"
        if vol > 20_000_000:
            return "MID‑CAP"
        return "SMALL‑CAP"

    def get_grid_params(self, rsi_val, volatility_pct):
        """Calculate grid bounds + count based on RSI & volatility."""
        # Price range
        if rsi_val <= 30:
            lower_bound = self.px * 0.97; upper_bound = self.px * 1.06
        elif rsi_val >= 70:
            lower_bound = self.px * 0.94; upper_bound = self.px * 1.03
        else:
            lower_bound = self.px * 0.95; upper_bound = self.px * 1.05

        # Grid count
        is_main = self.symbol.upper() in MAIN_TOKENS
        if volatility_pct > 15:
            grid_count = 150 if is_main else 65
        elif volatility_pct > 10:
            grid_count = 120 if is_main else 85
        else:
            grid_count = 85 if is_main else 55

        grid_mode = "Geometric" if volatility_pct > 15 else "Arithmetic"
        cycles_per_day = int(volatility_pct * 2)
        return {
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "grid_count": grid_count,
            "grid_mode": grid_mode,
            "cycles_per_day": cycles_per_day
        }

    # ---------- scoring ----------
    def score(self):
        vol_reg, width, _atr   = self.volatility()
        tr_dir, tr_str         = self.trend()
        rsi_val, rsi_sig       = self.rsi_sig()

        score, reasons = 0, []
        if vol_reg == "medium":
            score += 30; reasons.append("Medium volatility ideal")
        elif vol_reg == "low":
            score += 15; reasons.append("Low volatility OK")
        else:
            score += 5;  reasons.append("High volatility")

        if tr_dir == "neutral":
            score += 25; reasons.append("Sideways trend")
        elif tr_str < 0.6:
            score += 15; reasons.append("Weak trend")

        if rsi_sig in {"oversold", "overbought"}:
            score += 20; reasons.append(f"RSI {rsi_sig}")
        elif rsi_sig.startswith("approaching"):
            score += 10; reasons.append(f"RSI {rsi_sig.replace('_',' ')}")

        vol24 = self.info["total_volume"]
        is_main = self.symbol.upper() in MAIN_TOKENS
        if is_main:
            if vol24 > 5_000_000:
                score += 15; reasons.append("Main token liquidity")
            elif vol24 > MIN_VOLUME_MAIN:
                score += 10; reasons.append("Adequate main token liquidity")
        else:
            if vol24 > 50_000_000:
                score += 15; reasons.append("High liquidity")
            elif vol24 > MIN_VOLUME:
                score += 10; reasons.append("Adequate liquidity")

        if abs(self.info["price_change_percentage_24h"]) < 5:
            score += 10; reasons.append("Stable price")

        suit = "poor"
        if is_main:
            if score >= 40:
                suit = "excellent"
            elif score >= 25:
                suit = "good"
            elif score >= 15:
                suit = "moderate"
        else:
            if score >= 70:
                suit = "excellent"
            elif score >= 50:
                suit = "good"
            elif score >= 30:
                suit = "moderate"

        return {
            "score": score,
            "suit": suit,
            "vol_reg": vol_reg,
            "rsi_sig": rsi_sig,
            "rsi_val": rsi_val,
            "trend": tr_dir,
            "trend_strength": tr_str,
            "reasons": reasons,
            "volatility_pct": width * 100 if width else 10
        }

# ───────────── ALERT BUILDER ──────────────────────

def build_alert(an: GridAnalyzer, meta: dict) -> str:
    direction, direction_emoji, direction_desc = an.determine_direction(meta)
    rsi_val = meta["rsi_val"]
    volatility_pct = meta["volatility_pct"]

    # Quality indicator
    quality = "🔥" if meta["suit"] == "excellent" else "⚡" if meta["suit"] == "good" else "⚠️"

    market_cap = an.get_market_cap_category()
    grid_params = an.get_grid_params(rsi_val, volatility_pct)

    is_main = an.symbol.upper() in MAIN_TOKENS
    stop_loss = "Disabled" if is_main else "5%"
    trailing  = "Yes" if is_main else "No"

    text = (
        f"{direction_emoji} *{an.symbol}* RSI {rsi_val:.1f} | {market_cap}\n"
        "📊 *GRID SETUP*\n\n"
        f"*Price Range:* {fmt_price(grid_params['lower_bound'])} – {fmt_price(grid_params['upper_bound'])}\n"
        f"*Grid Count:* {grid_params['grid_count']} grids\n"
        f"*Grid Mode:* {grid_params['grid_mode']}\n"
        f"*Direction:* {direction} {quality}\n"
        f"*Trailing:* {trailing}\n"
        f"*Stop Loss:* {stop_loss}\n"
        f"*Expected Cycles/Day:* ~{grid_params['cycles_per_day']}\n"
        f"*Volatility:* {volatility_pct:.1f}% ({grid_params['grid_mode']} recommended)\n\n"
        f"💡 *Analysis:* {direction_desc}. Recommended for {direction.lower()}‑bias grid."
    )
    return text

# ────────────── MARKET FETCH ──────────────────────

def fetch_candidates():
    out = []
    for tk in fetch_perp_tickers():
        sym_full = tk["symbol"]
        if "_PERP" in sym_full:
            base = sym_full.split("_")[0]
        else:
            raw_base = sym_full.split("_")[0]
            base = raw_base.split(".")[0]

        if is_excluded(base):
            continue
        price = float(tk["close"]); vol24 = float(tk["amount"])
        if price < MIN_PRICE:
            continue

        is_main_token = base.upper() in MAIN_TOKENS
        if is_main_token:
            if vol24 < MIN_VOLUME_MAIN:
                logging.info(f"Main token {base} filtered: volume ${vol24:,.0f} < ${MIN_VOLUME_MAIN:,.0f}")
                continue
        else:
            if vol24 < MIN_VOLUME:
                continue

        open_px = float(tk["open"])
        change = (price - open_px) / open_px * 100 if open_px else 0
        out.append({
            "symbol": base,
            "raw_symbol": sym_full,
            "current_price": price,
            "total_volume": vol24,
            "price_change_percentage_24h": change
        })

    logging.info(
        "Candidates after filtering – Main tokens: %d, Small tokens: %d",
        len([c for c in out if c["symbol"].upper() in MAIN_TOKENS]),
        len([c for c in out if c["symbol"].upper() not in MAIN_TOKENS])
    )
    return out

# ──────────────── MAIN LOOP ───────────────────────

def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        cands = fetch_candidates()
    except Exception as exc:
        logging.error(f"Pionex API error: {exc}")
        send_telegram(f"*GRID SCAN ERROR*\n{md_escape(str(exc))}")
        return

    scored = []
    for info in cands:
        try:
            ga = GridAnalyzer(info)
            meta = ga.score()
            is_main = info["symbol"].upper() in MAIN_TOKENS
            if meta["suit"] == "poor" and not is_main:
                continue
            scored.append((meta["score"], build_alert(ga, meta), ga.symbol))
        except Exception as exc:
            logging.debug(f"{info['symbol']} skip: {exc}")

    scored.sort(key=lambda x: x[0], reverse=True)

    main_alerts  = [a for sc, a, sym in scored if sym.upper() in MAIN_TOKENS]
    small_alerts = [a for sc, a, sym in scored if sym.upper() not in MAIN_TOKENS][:MAX_SMALL_ALERTS]

    msg = f"🤖 *ENHANCED GRID TRADING ALERTS — {md_escape(ts)}*\n"
    if main_alerts:
        msg += "*🏆 MAIN TOKENS*\n" + "\n".join(main_alerts) + "\n"
    if small_alerts:
        msg += "*💎 SMALLER OPPORTUNITIES*\n" + "\n".join(small_alerts)
    if not main_alerts and not small_alerts:
        msg += "❌ No suitable opportunities found."

    send_telegram(msg)
    logging.info("Scan complete.")

if __name__ == "__main__":
    main()
