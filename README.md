🧠 RSI & Grid Scanner Bot Description

This bot monitors hourly market data for cryptocurrencies using the CoinGecko API. It performs two main functions:

🔻 RSI Monitoring:

Tracks BTC, ETH, SOL, and HYPE hourly RSI (Relative Strength Index).

Sends an alert when RSI drops below a certain threshold (typically 35), signaling potential entry points.

Suggests Futures Grid Bot parameters such as:

Price Range

Number of Grids (dynamic based on volatility)

Grid Mode (Arithmetic)

Trailing (Disabled)

Direction (Long)

📊 Sideways Market Scanner:

Scans hundreds of cryptocurrencies every hour to identify coins trading in a sideways range.

Selects up to 5 trending candidates (excluding BTC, ETH, SOL, HYPE) with flat price action.

Recommends suitable Grid Bot parameters for each.

All market data (prices, sparkline trends, RSI calculations) are powered by the CoinGecko API.

The Telegram bot sends a unified alert combining both RSI-based signals and sideways coin suggestions to help you start a Futures Grid Bot confidently with real-time, data-driven insights.
