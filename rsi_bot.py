import requests
import time
from datetime import datetime, timezone
import os
import logging

# ─────────────────────────────────────────
# Logging
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# ─────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN',
                             '7998783762:AAHvT55g8H-4UlXdGLCchfeEiryUjTF7jk8')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '7588547693')

COINGECKO_API = 'https://api.coingecko.com/api/v3'
PIONEX_API    = 'https://api.pionex.com/api/v1'

MIN_VOLUME = 10_000_000
MIN_PRICE  = 0.01

# Main tokens (HYPE added explicitly)
MAIN_TOKENS   = ['bitcoin', 'ethereum', 'solana',
                 'hyperliquid', 'hyperliquid-hype']
HYPE_VARIANTS = ['hyperliquid', 'hyperliquid-hype']

# Cache of Pionex‑supported PERP bases
PIONEX_SUPPORTED_TOKENS = set()

# ─────────────────────────────────────────
# Fetch PERP/USDT bases from Pionex
# ─────────────────────────────────────────
def get_pionex_supported_tokens():
    """Return a cached set of base symbols that have an enabled PERP/USDT contract."""
    global PIONEX_SUPPORTED_TOKENS
    if PIONEX_SUPPORTED_TOKENS:
        return PIONEX_SUPPORTED_TOKENS

    try:
        logging.info("Fetching supported tokens from Pionex API…")
        url  = f"{PIONEX_API}/common/symbols"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if 'data' not in data or 'symbols' not in data['data']:
            logging.error("Unexpected Pionex API response format")
            return set()

        supported_tokens = set()
        spot_pairs, perp_pairs = 0, 0

        for s in data['data']['symbols']:
            if not s.get('enable', False):
                continue

            symbol_type    = s.get('type', '')
            base_currency  = s.get('baseCurrency', '').upper()
            quote_currency = s.get('quoteCurrency', '').upper()

            # — PERP focus only —──────────────────────────────────────────
            if symbol_type == 'PERP' and quote_currency == 'USDT' and base_currency:
                supported_tokens.add(base_currency)
                perp_pairs += 1
            elif symbol_type == 'SPOT':
                spot_pairs += 1  # counted only for statistics
        # ————————————————————————————————————————————————

        PIONEX_SUPPORTED_TOKENS = supported_tokens
        logging.info(f"Pionex supports {len(supported_tokens)} PERP tokens with USDT pairs")
        logging.info(f"Found {perp_pairs} PERP pairs and {spot_pairs} SPOT pairs (ignored)")
        logging.info(f"Sample PERP bases: {list(supported_tokens)[:10]}")
        return supported_tokens

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch Pionex supported tokens: {e}")
        return set()
    except Exception as e:
        logging.error(f"Error processing Pionex API response: {e}")
        return set()

# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def map_coingecko_to_pionex_symbol(sym):
    return {'WBTC': 'BTC', 'WETH': 'ETH'}.get(sym.upper(), sym.upper())

def is_token_supported_on_pionex(cg_symbol, cg_id):
    supported = get_pionex_supported_tokens()
    if not supported:
        logging.warning("Supported-token cache empty—allowing all tokens")
        return True
    return map_coingecko_to_pionex_symbol(cg_symbol) in supported

def send_telegram(msg):
    if not TELEGRAM_TOKEN.strip() or not TELEGRAM_CHAT_ID.strip():
        logging.warning("Telegram creds empty—skip sending")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
                      timeout=10).raise_for_status()
        logging.info("Telegram message sent")
    except requests.exceptions.RequestException as e:
        logging.error(f"Telegram send failed: {e}")
        time.sleep(60)
        try:
            requests.post(url, data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
                          timeout=10).raise_for_status()
            logging.info("Telegram retry succeeded")
        except Exception as e2:
            logging.error(f"Telegram retry failed: {e2}")

# ─────────────────────────────────────────
# Market‑data fetch & filtering
# ─────────────────────────────────────────
def fetch_market_data():
    logging.info("Fetching market data from CoinGecko…")
    url = (f"{COINGECKO_API}/coins/markets?vs_currency=usd&order=market_cap_desc"
           "&per_page=250&page=1&sparkline=true")
    r = requests.get(url, timeout=10)
    r.raise_for_status()

    get_pionex_supported_tokens()  # cache PERP whitelist first

    data, vol_price_filt, pionex_filt = [], 0, 0
    for coin in r.json():
        if coin['total_volume'] <= MIN_VOLUME or coin['current_price'] <= MIN_PRICE:
            vol_price_filt += 1
            continue
        if not is_token_supported_on_pionex(coin['symbol'], coin['id']):
            pionex_filt += 1
            continue
        data.append(coin)

    logging.info("Filtering results:")
    logging.info(f"  · Volume/price filtered: {vol_price_filt}")
    logging.info(f"  · Not PERP‑supported on Pionex: {pionex_filt}")
    logging.info(f"  · Remaining tokens: {len(data)}")

    main_tokens = [c for c in data if c['id'] in MAIN_TOKENS]
    sorted_data = sorted(data, key=lambda x: x['market_cap'], reverse=True)
    smaller_tokens = [c for i, c in enumerate(sorted_data)
                      if i >= 20 and c['id'] not in MAIN_TOKENS]

    # direct fetch for missing mains (handles HYPE variants)
    missing = [t for t in MAIN_TOKENS if t not in {c['id'] for c in main_tokens}]
    for token_id in missing:
        for var in (HYPE_VARIANTS if token_id == 'hyperliquid' else [token_id]):
            try:
                d_url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={var}&sparkline=true"
                d = requests.get(d_url, timeout=10).json()
                if (d and d[0]['total_volume'] > MIN_VOLUME
                        and d[0]['current_price'] > MIN_PRICE
                        and is_token_supported_on_pionex(d[0]['symbol'], d[0]['id'])):
                    main_tokens.append(d[0])
                    break
            except Exception as e:
                logging.error(f"Direct fetch for {var} failed: {e}")

    return main_tokens + smaller_tokens

# ─────────────────────────────────────────
# RSI, volatility & grid logic (unchanged)
# ─────────────────────────────────────────
def calc_rsi(prices):
    if len(prices) < 15:
        return None
    gains, losses = [], []
    for i in range(1, 15):
        delta = prices[-i] - prices[-(i + 1)]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_volatility(prices):
    if len(prices) < 5:
        return 0.05
    returns = [abs(prices[i] / prices[i-1] - 1)
               for i in range(1, len(prices)) if prices[i-1] > 0]
    return sum(returns) / len(returns) if returns else 0.05

def get_market_cap_tier(mcap):
    return ('mega' if mcap >= 50_000_000_000 else
            'large' if mcap >= 10_000_000_000 else
            'mid'   if mcap >=  1_000_000_000 else 'small')

def format_price(v):
    return (f"${v:.2f}"  if v >= 100 else
            f"${v:.4f}"  if v >=   1 else
            f"${v:.6f}"  if v >= 0.01 else
            f"${v:.10f}")

def get_enhanced_grid_setup(coin, rsi):
    current_price = coin['current_price']
    sparkline     = coin['sparkline_in_7d']['price'][-20:]
    mcap          = coin['market_cap']
    volume        = coin['total_volume']

    vol  = calculate_volatility(sparkline)
    tier = get_market_cap_tier(mcap)

    tier_params = {
        "mega":  {"base_spacing": 0.003, "safety_buffer": 0.08, "max_grids": 200},
        "large": {"base_spacing": 0.004, "safety_buffer": 0.10, "max_grids": 150},
        "mid":   {"base_spacing": 0.006, "safety_buffer": 0.12, "max_grids": 100},
        "small": {"base_spacing": 0.008, "safety_buffer": 0.15, "max_grids":  80},
    }
    p = tier_params[tier]

    if   vol > 0.20: mult, mode = 2.0, "Geometric"
    elif vol > 0.15: mult, mode = 1.6, "Arithmetic"
    elif vol > 0.08: mult, mode = 1.2, "Arithmetic"
    else:            mult, mode = 0.8, "Arithmetic"
    spacing_pct = p["base_spacing"] * mult

    recent_min, recent_max = min(sparkline), max(sparkline)
    sb = p["safety_buffer"]
    if   rsi <= 25: lb, ub = sb*0.6, sb*1.4
    elif rsi <= 35: lb, ub = sb*0.8, sb*1.2
    elif rsi >= 75: lb, ub = sb*1.4, sb*0.6
    elif rsi >= 65: lb, ub = sb*1.2, sb*0.8
    else:           lb = ub = sb

    min_p = recent_min * (1-lb)
    max_p = recent_max * (1+ub)

    req_range = current_price * spacing_pct * 20
    if (max_p - min_p) < req_range:
        adj = (req_range - (max_p - min_p)) / 2
        min_p -= adj
        max_p += adj

    grid_spacing = current_price * spacing_pct
    grids_theory = (max_p - min_p) / grid_spacing
    max_grids = p["max_grids"]
    if volume > 100_000_000:
        max_grids = int(max_grids * 1.3)
    elif volume < 20_000_000:
        max_grids = int(max_grids * 0.7)
    grids = max(15, min(max_grids, int(grids_theory)))

    if   rsi <= 30: direction, conf = "Long",  "High"
    elif rsi <= 40: direction, conf = "Long",  "Medium"
    elif rsi >= 70: direction, conf = "Short", "High"
    elif rsi >= 60: direction, conf = "Short", "Medium"
    else:           direction, conf = "Neutral","High"

    daily_cycles = int(vol * 100 * 2)
    trailing  = "Yes" if tier in ["mega","large"] and direction!="Neutral" else "No"
    stop_loss = "5%"  if tier=="small" and direction!="Neutral" else "Disabled"

    return {
        'min_price': min_p,
        'max_price': max_p,
        'grids': grids,
        'mode': mode,
        'direction': direction,
        'direction_confidence': conf,
        'spacing': grid_spacing,
        'volatility': vol,
        'market_tier': tier,
        'trailing': trailing,
        'stop_loss': stop_loss,
        'expected_daily_cycles': daily_cycles
    }

# ─────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────
def main():
    try:
        logging.info("Starting PERP‑only grid analysis…")
        market_data = fetch_market_data()
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        if not market_data:
            send_telegram(f"🤖 PIONEX GRID ALERT — {ts}\n❌ No suitable PERP grid "
                          "opportunities right now.\n💡 Check back later!")
            return

        supported_cnt = len(get_pionex_supported_tokens())
        main_alerts, small_alerts = [], []

        for coin in market_data:
            rsi = calc_rsi(coin['sparkline_in_7d']['price'][-15:])
            if rsi is None:
                continue

            gp = get_enhanced_grid_setup(coin, rsi)
            sym = coin['symbol'].upper()

            alert = (f"{ {'Long':'🟢','Short':'🔴','Neutral':'🟡'}[gp['direction']] } "
                     f"{sym} RSI {rsi:.1f} | {gp['market_tier'].upper()}‑CAP\n"
                     f"📊 PIONEX GRID SETUP\n"
                     f"• Price Range: {format_price(gp['min_price'])}"
                     f"‑{format_price(gp['max_price'])}\n"
                     f"• Grid Count: {gp['grids']} grids\n"
                     f"• Grid Mode: {gp['mode']}\n"
                     f"• Direction: {gp['direction']} "
                     f"{'🔥' if gp['direction_confidence']=='High' else '⚡'}\n"
                     f"• Trailing: {gp['trailing']}\n"
                     f"• Stop Loss: {gp['stop_loss']}\n"
                     f"• Expected Cycles/Day: ~{gp['expected_daily_cycles']}\n"
                     f"• Volatility: {gp['volatility']:.1%}\n")

            reason = ("Oversold → Long grid" if rsi <= 35 else
                      "Overbought → Short grid" if rsi >= 65 else
                      "Neutral RSI → range‑bound grid")
            alert += f"\n💡 Analysis: {reason}"

            (main_alerts if coin['id'] in MAIN_TOKENS else small_alerts).append(alert)

        msg = (f"🤖 PIONEX GRID TRADING ALERTS — {ts}\n"
               f"📊 Analyzed {supported_cnt} PERP bases on Pionex\n\n")

        if main_alerts:
            msg += "🏆 MAIN TOKENS\n" + '\n\n'.join(main_alerts) + '\n\n'
        if small_alerts:
            msg += "💎 SMALLER OPPORTUNITIES\n" + '\n\n'.join(small_alerts[:2])

        if not main_alerts and not small_alerts:
            msg += ("❌ No suitable grid opportunities this hour.\n"
                    "⏳ Market may be too stable/volatile.\n")

        send_telegram(msg)
        logging.info("PERP‑only grid analysis completed")

    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {e}")
        send_telegram(f"🚨 Pionex Grid Bot API Error: {e}")
    except Exception as e:
        logging.error(f"Unexpected Error: {e}")
        send_telegram(f"🚨 Pionex Grid Bot Unexpected Error: {e}")

if __name__ == "__main__":
    main()
