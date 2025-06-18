def analyse(sym, interval="5M", limit=400, use_grid_height=True):
    closes = fetch_closes(sym, interval, limit=limit)
    if len(closes) < 60:
        return None
    
    px = closes[-1]
    grid_height = 0.15 if px < 0.1 else 0.05  # Use 15% for small coins
    low = px * (1 - grid_height / 2)
    high = px * (1 + grid_height / 2)
    rng = high - low
    
    if rng <= 0 or px == 0:
        return None
    
    pos = (px - low) / rng
    
    if POSITION_THRESHOLD <= pos <= (1 - POSITION_THRESHOLD):
        logging.debug(f"{sym}: Price too centered in range ({pos:.3f}), skipping")
        return None
    
    std = compute_std_dev(closes)
    vol = rng / px * 100
    vf = max(0.1, vol + std * 100)
    spacing = max(SPACING_MIN, min(SPACING_MAX, SPACING_TARGET * (30 / max(vf, 1))))
    use_fixed_grids = True  # Always use centered range
    grids = calculate_grids(rng, px, spacing, vol, use_fixed_grids)
    cycle = round((grids * spacing) / (vf + 1e-9) * 2, 1)
    
    if cycle > CYCLE_MAX or cycle <= 0:
        return None
    
    if px < low * (1 - STOP_BUFFER) or px > high * (1 + STOP_BUFFER):
        low = min(px, low * 0.95)
        high = max(px, high * 1.05)
    
    # Add trend filter
    regime = regime_type(std, vol)
    if regime == "Trending":
        logging.debug(f"{sym}: Market trending, skipping")
        return None
    
    rsi = compute_rsi(closes)
    bb_lower, bb_upper = compute_bollinger_bands(closes)
    macd_line, signal_line, macd_hist = compute_macd(closes)
    logging.debug(f"{sym}: RSI={rsi:.1f}, BB={px < bb_lower if bb_lower else False}, MACD={macd_line > signal_line if macd_line else False}")
    
    regime = regime_type(std, vol)
    
    rsi_long_threshold = RSI_OVERSOLD
    rsi_short_threshold = RSI_OVERBOUGHT
    
    rsi_signal_long = rsi < rsi_long_threshold
    rsi_signal_short = rsi > rsi_short_threshold
    bb_signal_long = bb_lower is not None and px < bb_lower
    bb_signal_short = bb_upper is not None and px > bb_upper
    macd_signal_long = macd_line is not None and macd_line > signal_line
    macd_signal_short = macd_line is not None and macd_line < signal_line
    
    if px < 0.1:
        if rsi_signal_long or bb_signal_long or macd_signal_long:
            zone_check = "Long"
        elif rsi_signal_short or bb_signal_short or macd_signal_short:
            zone_check = "Short"
        else:
            return None
    else:
        long_signals = sum([rsi_signal_long, bb_signal_long, macd_signal_long])
        short_signals = sum([rsi_signal_short, bb_signal_short, macd_signal_short])
        if long_signals >= 2:
            zone_check = "Long"
            logging.info(f"{sym}: Long signal - RSI:{rsi_signal_long}, BB:{bb_signal_long}, MACD:{macd_signal_long} ({long_signals}/3)")
        elif short_signals >= 2:
            zone_check = "Short"
            logging.info(f"{sym}: Short signal - RSI:{rsi_signal_short}, BB:{bb_signal_short}, MACD:{macd_signal_short} ({short_signals}/3)")
        else:
            return None
    
    orders, stop_reason = simulate_grid_orders(sym, low, high, grids, spacing, px, closes, capital=100, leverage=10)
    if stop_reason:
        return None
    
    result = dict(
        symbol=sym,
        zone=zone_check,
        low=low,
        high=high,
        now=px,
        grids=grids,
        spacing=round(spacing, 2),
        vol=round(vol, 1),
        std=round(std, 5),
        cycle=cycle,
        orders=orders
    )
    
    logging.info(f"Valid signal found for {sym}: {zone_check} zone, vol={vol:.1f}%, score={score_signal(result)}")
    return result
