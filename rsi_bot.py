import os, json, math, logging, time, requests
import numpy as np
from pathlib import Path

# ── ENV + CONFIG ────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

API = "https://api.pionex.com/api/v1"
TOP_N = 100
MIN_NOTIONAL_USD = 1_000_000
SPACING_MIN = 0.3
SPACING_MAX = 1.2
SPACING_TARGET = 0.75
CYCLE_MAX = 5.0  # Relaxed for testing
STOP_BUFFER = 0.01
STATE_FILE = Path("active_grids.json")
VOL_THRESHOLD = 0.5  # Lowered for testing
WRAPPED = {"WBTC", "WETH", "WSOL", "WBNB"}
STABLE = {"USDT", "USDC", "BUSD", "DAI"}
EXCL = {"LUNA", "LUNC", "USTC"}
ZONE_EMO = {"Long": "🟢 Long", "Short": "🔴 Short"}
last_trade_time = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── TELEGRAM ────────────────────────────────────────
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        logging.error("Missing Telegram credentials")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        r.raise_for_status()
        logging.info("Telegram Response: %s", r.json())
    except requests.exceptions.RequestException as e:
        logging.error("Telegram error: %s, Response: %s", e, r.text if 'r' in locals() else "No response")

# ── SYMBOL FETCHING ─────────────────────────────────
def valid(sym):
    u = sym.upper()
    return (u.split("_")[0] not in WRAPPED | STABLE | EXCL and 
            not u.endswith(("UP", "DOWN", "3L", "3S", "5L", "5S")))

def fetch_symbols(retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(f"{API}/market/tickers", params={"type": "PERP"}, timeout=10)
            r.raise_for_status()
            tickers = r.json().get("data", {}).get("tickers", [])
            logging.info("Raw tickers: %s", [t["symbol"] for t in tickers])
            pairs = [t for t in tickers if valid(t["symbol"]) and float(t.get("amount", 0)) > MIN_NOTIONAL_USD]
            pairs.sort(key=lambda x: float(x["amount"]), reverse=True)
            symbols = [p["symbol"] for p in pairs][:TOP_N]
            logging.info("Fetched %d valid symbols: %s", len(symbols), symbols)
            return symbols
        except Exception as e:
            logging.warning("Failed to fetch symbols, attempt %d: %s", attempt + 1, e)
            time.sleep(2)
    logging.error("Failed to fetch symbols after %d retries", retries)
    return []

# ── FETCH CLOSES WITH LIMIT ─────────────────────────
def fetch_closes(sym, interval="5M", limit=400, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(
                f"{API}/market/klines",
                params={"symbol": sym, "interval": interval, "limit": limit, "type": "PERP"},
                timeout=10
            )
            r.raise_for_status()
            payload = r.json().get("data", {})
            logging.info("Raw API response for %s (%s): %s", sym, interval, r.json())
            kl = payload.get("klines") or payload
            closes = []
            for k in kl:
                if isinstance(k, dict) and "close" in k:
                    closes.append(float(k["close"]))
                elif isinstance(k, (list, tuple)) and len(k) >= 5:
                    closes.append(float(k[4]))
            logging.info("Fetched %d closes for %s (%s)", len(closes), sym, interval)
            return closes
        except Exception as e:
            logging.warning("API error for %s (%s), attempt %d: %s", sym, interval, attempt + 1, e)
            time.sleep(2)
    logging.error("Failed to fetch closes for %s after %d retries", sym, retries)
    return []

# ── ANALYSIS FUNCTIONS ──────────────────────────────
def compute_std_dev(closes, period=30):
    return float(np.std(closes[-period:])) if len(closes) >= period else 0

def compute_cooldown(vol_pct, std_dev):
    base = 300
    extra = max(0, (vol_pct - 1) + (std_dev - 0.01) * 100) * 60
    return base + extra

def should_trigger(sym, vol_pct, std_dev):
    now = time.time()
    cooldown = compute_cooldown(vol_pct, std_dev)
    if now - last_trade_time.get(sym, 0) >= cooldown:
        last_trade_time[sym] = now
        return True
    return False

def calculate_grids(rng, px, spacing, vol):
    base = rng / (px * spacing / 100)
    if vol < 1.5:
        return max(4, min(200, math.floor(base / 2)))
    else:
        return max(10, min(200, math.floor(base)))

def grid_type_hint(rng_pct, vol):
    if rng_pct < 1.5 and vol < 1.2:
        return "Arithmetic"
    return "Geometric"

def money(p):
    return f"${p:.8f}" if p < 0.1 else f"${p:,.4f}" if p < 1 else f"${p:,.2f}"

def score_signal(d):
    return round(
        d["vol"] * 2 +
        ((200 - d["grids"]) / 200) * 10 +
        ((1.5 - min(d["spacing"], 1.5)) * 15) +
        (1.5 / max(d["cycle"], 0.1)) * 10,
        1
    )

# ── START_MSG FUNCTION ──────────────────────────────
def start_msg(d, rank=None):
    score = score_signal(d)
    lev = "20x–50x" if d["spacing"] <= 0.5 else "10x–25x" if d["spacing"] <= 0.75 else "5x–15x"
    mode = grid_type_hint((d["high"] - d["low"]) / d["now"] * 100, d["vol"])
    total_seconds = d["cycle"] * 24 * 3600
    days = int(total_seconds // (24 * 3600))
    remaining_seconds = total_seconds % (24 * 3600)
    hours = int(remaining_seconds // 3600)
    minutes = int((remaining_seconds % 3600) // 60)
    cycle_time = f"{days} Day(s) {hours} Hour(s) {minutes} Minute(s)" if days > 0 else f"{hours} Hour(s) {minutes} Minute(s)"
    prefix = f"🥇 Top {rank} — {d['symbol']}" if rank else f"📈 Start Grid Bot: {d['symbol']}"
    return (f"{prefix}\n"
            f"📊 Range: {money(d['low'])} – {money(d['high'])}\n"
            f"📈 Entry Zone: {ZONE_EMO[d['zone']]}\n"
            f"🧮 Grids: {d['grids']} | 📏 Spacing: {d['spacing']}%\n"
            f"🌪️ Volatility: {d['vol']}% | ⏱️ Cycle: {cycle_time}\n"
            f"🌀 Score: {score} | ⚙️ Leverage Hint: {lev}\n"
            f"🔧 Grid Mode Hint: {mode}\n"
            f"⚠️ Set range in Pionex AI Grid Bot for optimal performance")

def stop_msg(sym, reason, info):
    closes = fetch_closes(sym, interval="5M", limit=1)
    now = closes[-1] if closes and closes else (info["low"] + info["high"]) / 2
    return (f"🛑 Exit Alert: {sym}\n"
            f"📉 Reason: {reason}\n"
            f"📊 Range: {money(info['low'])} – {money(info['high'])}\n"
            f"💱 Current Price: {money(now)}")

# ── UPDATED ANALYSE FUNCTION ────────────────────────
def analyse(sym, interval="5M", limit=400):
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 20:
        logging.warning("Insufficient data for %s (%s): %d closes", sym, interval, len(closes))
        return None

    # Bollinger Bands for price range (mimics Pionex AI)
    mid = np.mean(closes[-20:])
    std = np.std(closes[-20:])
    low = mid - 2 * std
    high = mid + 2 * std
    px = closes[-1]
    rng = high - low
    if rng <= 0 or px == 0:
        logging.warning("Invalid range for %s (%s): rng=%.2f, px=%.2f", sym, interval, rng, px)
        return None

    pos = (px - low) / rng
    std = compute_std_dev(closes)
    vol = rng / px * 100
    vf = max(0.1, vol + std * 100)
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(vf, 1))))
    grids = calculate_grids(rng, px, spacing, vol)
    cycle = round((grids * spacing) / (vf + 1e-9) * 2, 1)
    if cycle > CYCLE_MAX or cycle <= 0:
        logging.warning("Invalid cycle for %s (%s): cycle=%.2f", sym, interval, cycle)
        return None

    zone = "Long" if pos < 0.5 else "Short"
    logging.info("Analyse %s (%s): low=%.2f, high=%.2f, px=%.2f, pos=%.2f, vol=%.2f, std=%.5f, cycle=%.1f",
                 sym, interval, low, high, px, pos, vol, std, cycle)
    return dict(
        symbol=sym,
        zone=zone,
        low=low,
        high=high,
        now=px,
        grids=grids,
        spacing=round(spacing, 2),
        vol=round(vol, 1),
        std=round(std, 5),
        cycle=cycle
    )

# ── SCAN WITH FALLBACK ──────────────────────────────
def scan_with_fallback(sym, vol_threshold=VOL_THRESHOLD):
    for interval in ["5M", "15M", "1H"]:
        try:
            result = analyse(sym, interval)
            if result:
                logging.info("Analysis for %s (%s): vol=%.2f, cycle=%.2f, grids=%d", sym, interval, result.get('vol', 0), result.get('cycle', 0), result.get('grids', 0))
                if result.get('vol', 0) > vol_threshold and should_trigger(sym, result["vol"], result["std"]):
                    logging.info("Symbol %s passed with vol=%.2f", sym, result['vol'])
                    return result
                else:
                    logging.info("Symbol %s failed: vol=%.2f <= %.2f or cooldown", sym, result['vol'], vol_threshold)
            else:
                logging.warning("Analysis failed for %s (%s): result is None", sym, interval)
        except Exception as e:
            logging.warning("Exception analyzing %s (%s): %s", sym, interval, e)
            continue
    logging.warning("No valid analysis for %s", sym)
    return None

# ── STATE MANAGEMENT ────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                content = f.read().strip()
                return json.loads(content) if content else {}
        except json.JSONDecodeError:
            logging.warning("Invalid JSON in %s, returning empty state", STATE_FILE)
            return {}
    return {}

def save_state(d):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(d, f, indent=2)
        logging.info("State saved successfully")
    except IOError as e:
        logging.error("Error saving state file: %s", e)

# ── CHECK_CYCLE_NOTIFICATION FUNCTION ───────────────
def check_cycle_notification(start_time, cycle, sym, warned=False):
    if not start_time or not cycle or warned:
        return False
    current_time = time.time()
    elapsed_time = current_time - start_time
    cycle_seconds = cycle * 24 * 3600
    threshold = max(3600, cycle_seconds * 0.1)
    remaining = cycle_seconds - elapsed_time
    if 0 < remaining <= threshold:
        remaining_seconds = remaining
        days = int(remaining_seconds // (24 * 3600))
        remaining_seconds %= (24 * 3600)
        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)
        remaining_time = f"{days} Day(s) {hours} Hour(s) {minutes} Minute(s)" if days > 0 else f"{hours} Hour(s) {minutes} Minute(s)"
        cycle_seconds_total = cycle * 24 * 3600
        days_total = int(cycle_seconds_total // (24 * 3600))
        remaining_seconds_total = cycle_seconds_total % (24 * 3600)
        hours_total = int(remaining_seconds_total // 3600)
        minutes_total = int((remaining_seconds_total % 3600) // 60)
        cycle_time = f"{days_total} Day(s) {hours_total} Hour(s) {minutes_total} Minute(s)" if days_total > 0 else f"{hours_total} Hour(s) {minutes_total} Minute(s)"
        tg(f"⚠️ Cycle Warning: {sym}\n"
           f"Estimated cycle completion: {cycle_time}\n"
           f"Time remaining: {remaining_time}\n"
           f"Consider reviewing or stopping the bot.")
        return True
    return False

# ── MAIN FUNCTION ───────────────────────────────────
def main():
    tg("Test: Bot started at 2025-06-17 10:07 PM +08")
    prev = load_state()
    nxt, scored, stops = {}, [], []
    current_time = time.time()

    # Hardcode symbols for testing
    symbols = ["BTCUSDT_PERP", "ETHUSDT_PERP"]
    logging.info("Testing with symbols: %s", symbols)

    for sym in symbols:
        res = scan_with_fallback(sym)
        if not res:
            continue

        prev_state = prev.get(sym, {})
        warned = prev_state.get("warned", False)
        start_time = prev_state.get("start_time", current_time)

        if check_cycle_notification(start_time, res["cycle"], sym, warned):
            warned = True

        nxt[sym] = {
            "zone": res["zone"],
            "low": res["low"],
            "high": res["high"],
            "start_time": start_time,
            "warned": warned
        }

        if sym not in prev:
            scored.append((score_signal(res), res))
        else:
            p = prev[sym]
            if p["zone"] != res["zone"]:
                stops.append(stop_msg(sym, "Trend flip", res))
            elif res["now"] > p["high"] * (1 + STOP_BUFFER) or res["now"] < p["low"] * (1 - STOP_BUFFER):
                stops.append(stop_msg(sym, "Price exited range", res))

    for gone in set(prev) - set(nxt):
        mid = (prev[gone]["low"] + prev[gone]["high"]) / 2
        stop_message = stop_msg(gone, "No longer meets criteria", {
            "low": prev[gone]["low"],
            "high": prev[gone]["high"],
            "now": mid
        })
        stops.append(stop_message)
        tg(stop_message)

    save_state(nxt)

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        buf = ""
        for i, (_, r) in enumerate(scored, 1):
            m = start_msg(r, i)
            if len(buf) + len(m) > 3500:
                tg(buf)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            tg(buf)

    if stops:
        buf = ""
        for m in stops:
            if len(buf) + len(m) > 3500:
                tg(buf)
                buf = m + "\n\n"
            else:
                buf += m + "\n\n"
        if buf:
            tg(buf)

    logging.info("Analysis complete. New opportunities: %d, Stops: %d", len(scored), len(stops))

if __name__ == "__main__":
    main()
