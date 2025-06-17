Futures Grid Bot Scanner with Ranked Telegram Alerts
This Python script scans the futures market for grid bot opportunities by analyzing price volatility, grid parameters, and cycle times. It then ranks the potential opportunities using a composite score and sends emoji-enhanced alerts to your Telegram chat.

Features
Market Ticker Scanning: Retrieves symbols from Pionex's API based on criteria like notional volume and token exclusions.

Hybrid Analysis: Uses a dual-interval scan (5-minute and 60-minute) to ensure that the conditions are right for deploying a grid strategy while filtering out tokens with moderate price positions.

Composite Scoring: Calculates a score that takes into account:

Volatility: More dynamic price movements add to the score.

Grid Count: Fewer grids generally mean more efficient setups.

Spacing: Tighter spacing is favorable.

Cycle Time: Shorter cycle durations can indicate faster potential returns.

Suitability Messaging: Each signal is labeled with a suitability message:

🚀 Ideal for high-volatility scalping (score > 90)

🎯 Balanced for trend-following bots (score between 80 and 90)

📊 Moderate conditions — review before deploying (score ≤ 80)

Stateful Operation: Uses a JSON file (active_grids.json) to track previously active signals, ensuring that alerts are sent only for new or flipped opportunities.

Telegram Notifications: Sends alerts in ranked batches with emojis and actionable information for rapid decision-making.
