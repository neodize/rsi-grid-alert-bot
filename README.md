Crypto Grid Trading Alert Bot
A Python-based cryptocurrency monitoring bot that analyzes market conditions using RSI (Relative Strength Index) indicators and sends automated grid trading recommendations via Telegram.
🚀 Features

Automated Market Analysis: Monitors top 50 cryptocurrencies from CoinGecko API
RSI-Based Recommendations: Uses 14-period RSI to identify oversold/overbought conditions
Smart Grid Setup: Automatically calculates optimal grid trading parameters
Telegram Notifications: Sends formatted alerts with trading suggestions
Main Token Prioritization: Tracks key tokens (Bitcoin, Ethereum, Solana, Hyperliquid)
Volume & Price Filtering: Focuses on liquid assets with meaningful trading volume
Robust Error Handling: Includes retry logic and comprehensive logging

📊 How It Works
Market Data Collection

Fetches top 50 cryptocurrencies by market cap from CoinGecko
Filters coins based on:

Minimum trading volume: $10M+ daily
Minimum price: $0.01+
Excludes leveraged/synthetic tokens (ending in numbers + L/S)


Excludes top 20 coins to focus on smaller opportunities
Always includes main tokens: BTC, ETH, SOL, HYPE

Technical Analysis

RSI Calculation: Uses last 15 price points from 7-day sparkline data
Signal Generation:

RSI ≤ 35: Oversold → Long recommendation 🔻
RSI ≥ 65: Overbought → Short recommendation 🔺
RSI 35-65: Neutral → Range trading 📈

Grid Trading Setup
For each opportunity, the bot calculates:

Price Range: 5% below minimum to 5% above maximum recent prices
Grid Spacing: 0.5% of current price
Grid Count: 10-500 grids (dynamically calculated)
Mode: Arithmetic spacing
Direction: Long/Short/Neutral based on RSI
