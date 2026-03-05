import os
import time
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = 60
PRICE_MAX = 0.50
MIN_VOLUME = 100000
PUMP_THRESHOLD = 5


def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    })


def get_markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": 250,
        "page": 1,
        "sparkline": False
    }
    r = requests.get(url, params=params)
    return r.json()


def scan():
    markets = get_markets()

    for coin in markets:
        price = coin["current_price"]
        volume = coin["total_volume"]
        change = coin["price_change_percentage_24h"]

        if price is None or change is None:
            continue

        if price < PRICE_MAX and volume > MIN_VOLUME and change > PUMP_THRESHOLD:
            name = coin["symbol"].upper()
            msg = f"""
🚀 MICRO CAP RUNNER

Coin: {name}
Price: ${price}
24h Change: {round(change,2)}%
Volume: {volume}

Possible early momentum
"""
            print(msg)
            send_telegram(msg)


def main():
    send_telegram("Micro Cap Bot Started")

    while True:
        try:
            scan()
        except Exception as e:
            print("Error:", e)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
