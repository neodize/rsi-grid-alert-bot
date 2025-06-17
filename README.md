# RSI Grid Alert Bot

This Python script scans the Pionex perpetual futures market for high-probability grid bot setups. It identifies symbols showing strong directional bias, evaluates volatility, and calculates grid parameters. When conditions are met, it sends start/stop alerts via Telegram.

## 🔧 How It Works

For the top 100 most liquid symbols, the script:

1. **Fetches price history**
2. **Analyzes current price position** relative to recent high/low range
3. **Estimates volatility** and standard deviation
4. **Computes grid size, spacing, and cycle**
5. **Ranks results** based on a scoring formula
6. **Sends top-ranked opportunities** to Telegram

---

## 🧠 Key Concepts

| Term          | Meaning                                                                 |
|---------------|-------------------------------------------------------------------------|
| **Range**     | The price boundaries (lowest to highest) in recent history              |
| **Entry Zone**| "Long" if price is near the bottom of the range, "Short" if near top    |
| **Grid Spacing** | How far apart each buy/sell order is, in % — based on volatility     |
| **Grids**     | Number of levels the bot will place orders at within the range          |
| **Volatility**| How much price has moved relative to its current price (%)              |
| **Cycle**     | Estimated time (in days) for a full grid rotation, based on volatility  |
| **Score**     | Higher = better opportunity (based on volatility, grid count, spacing…) |
| **Leverage Hint** | Suggested leverage based on how tight the spacing is                |

---

