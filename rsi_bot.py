#!/usr/bin/env python3
"""
Debug version to identify why HYPE is not showing up
"""

import os
import logging
from datetime import datetime, timezone
import requests
import numpy as np

# Same config as original
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PIONEX_API       = "https://api.pionex.com"

MAIN_TOKENS      = {"BTC", "ETH", "SOL", "HYPE"}
MIN_VOLUME       = 10_000_000      # 24 h notional (quote currency)
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

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

def is_excluded(sym: str) -> bool:
    s = sym.upper()
    if s in WRAPPED_TOKENS or s in STABLECOINS or s in EXCLUDED_TOKENS:
        return True
    if s.endswith(("UP","DOWN","3L","3S","5L","5S")):
        return True
    return False

def fetch_perp_tickers():
    r = requests.get(f"{PIONEX_API}/api/v1/market/tickers",
                     params={"type": "PERP"}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["tickers"]

def debug_fetch_candidates():
    """Debug version that shows detailed info about HYPE processing"""
    out = []
    hype_found = False
    
    for tk in fetch_perp_tickers():
        sym_full = tk["symbol"]           # BTC_USDT  or  HYPE.PERP_USDT
        raw_base = sym_full.split("_")[0] # BTC   or  HYPE.PERP
        base     = raw_base.split(".")[0] # BTC   or  HYPE

        # Debug HYPE specifically
        if "HYPE" in sym_full.upper():
            hype_found = True
            print(f"\n🔍 HYPE FOUND:")
            print(f"  Full symbol: {sym_full}")
            print(f"  Raw base: {raw_base}")
            print(f"  Final base: {base}")
            print(f"  Ticker data: {tk}")
            
            # Check exclusion
            excluded = is_excluded(base)
            print(f"  Is excluded: {excluded}")
            if excluded:
                print(f"  ❌ HYPE excluded! Reason: checking exclusion logic...")
                if base.upper() in WRAPPED_TOKENS:
                    print(f"    - Found in WRAPPED_TOKENS")
                if base.upper() in STABLECOINS:
                    print(f"    - Found in STABLECOINS")
                if base.upper() in EXCLUDED_TOKENS:
                    print(f"    - Found in EXCLUDED_TOKENS")
                if base.upper().endswith(("UP","DOWN","3L","3S","5L","5S")):
                    print(f"    - Ends with leverage suffix")
                continue
                
            # Check price and volume filters
            price = float(tk["close"])
            vol24 = float(tk["amount"])
            print(f"  Price: ${price:.6f} (min: ${MIN_PRICE})")
            print(f"  Volume 24h: ${vol24:,.0f} (min: ${MIN_VOLUME:,.0f})")
            
            if price < MIN_PRICE:
                print(f"  ❌ HYPE failed price filter: ${price:.6f} < ${MIN_PRICE}")
                continue
            if vol24 < MIN_VOLUME:
                print(f"  ❌ HYPE failed volume filter: ${vol24:,.0f} < ${MIN_VOLUME:,.0f}")
                continue
                
            print(f"  ✅ HYPE passed basic filters!")

        # Continue with normal processing
        if is_excluded(base):
            continue
        price = float(tk["close"])
        vol24 = float(tk["amount"])
        if price < MIN_PRICE or vol24 < MIN_VOLUME:
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
    
    if not hype_found:
        print("\n❌ NO HYPE SYMBOL FOUND IN PIONEX PERP TICKERS!")
        print("Available symbols containing 'HYPE':")
        for tk in fetch_perp_tickers():
            if "HYPE" in tk["symbol"].upper():
                print(f"  - {tk['symbol']}")
    
    print(f"\nTotal candidates after filtering: {len(out)}")
    hype_in_candidates = any(c["symbol"].upper() == "HYPE" for c in out)
    print(f"HYPE in final candidates: {hype_in_candidates}")
    
    return out

# Test the debug function
if __name__ == "__main__":
    try:
        candidates = debug_fetch_candidates()
        
        # Also check what symbols we actually have
        print(f"\nFinal candidate symbols:")
        for c in candidates:
            symbol = c["symbol"].upper()
            is_main = symbol in MAIN_TOKENS
            print(f"  {c['symbol']} - Main token: {is_main}")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
