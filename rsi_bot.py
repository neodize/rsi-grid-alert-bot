import requests
import datetime
import pytz
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import telegram
import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def fetch_data():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 100,
        "page": 1,
        "sparkline": "true",
        "price_change_percentage": "1h,24h"
    }
    response = requests.get(url, params=params)
    return pd.DataFrame(response.json())

def calculate_trend_score(sparkline, interval_minutes=60):
    prices = np.array(sparkline)
    if len(prices) < 2:
        return 0
    returns = np.diff(prices) / prices[:-1]
    return np.mean(returns[-interval_minutes:]) * 100

def plot_sparkline(prices, name):
    plt.figure(figsize=(4, 1.5))
    sns.lineplot(x=range(len(prices)), y=prices)
    plt.xticks([])
    plt.yticks([])
    plt.title(name)
    plt.tight_layout()
    plot_path = f"/tmp/{name}.png"
    plt.savefig(plot_path)
    plt.close()
    return plot_path

def format_token_row(row):
    return f"{row['symbol'].upper()} | Score: {row['trend_score']:.2f}% | 1h: {row['price_change_percentage_1h_in_currency']:.2f}%, 24h: {row['price_change_percentage_24h_in_currency']:.2f}%"

def send_telegram_message(text, image_path=None):
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        if image_path:
            with open(image_path, 'rb') as img:
                bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=img, caption=text)
        else:
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    except Exception as e:
        print(f"Telegram send failed: {e}")

def main():
    df = fetch_data()

    # Clean and calculate trend score
    df = df[df['sparkline_in_7d'].notnull()]
    df['trend_score'] = df['sparkline_in_7d'].apply(lambda s: calculate_trend_score(s['price']))

    # Format symbol matching
    df['symbol'] = df['symbol'].str.lower()

    # Define MAIN TOKENS
    main_tokens = ['btc', 'eth', 'sol', 'hype']

    # Filter and sort
    main_df = df[df['symbol'].isin(main_tokens)].sort_values(by="trend_score", ascending=False)
    small_df = df[~df['symbol'].isin(main_tokens)].sort_values(by="trend_score", ascending=False).head(5)

    # Compose message
    timestamp = datetime.datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).strftime("%Y-%m-%d %H:%M")
    message = f"🧠 *Grid Bot Scanner*\n🕒 {timestamp} MYT\n\n"

    message += "🏆 *MAIN TOKENS*\n"
    if not main_df.empty:
        message += "\n".join([format_token_row(row) for _, row in main_df.iterrows()])
    else:
        message += "No main tokens available."

    message += "\n\n💎 *TOP 5 SMALL TOKENS*\n"
    if not small_df.empty:
        message += "\n".join([format_token_row(row) for _, row in small_df.iterrows()])
    else:
        message += "No small tokens found."

    # Send result
    send_telegram_message(message)

if __name__ == "__main__":
    main()
