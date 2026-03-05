import os
import time
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = 15

MIN_LIQUIDITY = 40000
MIN_VOLUME_5M = 20000
MIN_PRICE_CHANGE_5M = 6
MIN_TRADES_5M = 20

ALERTED = set()


def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    })


def get_pairs():
    url = "https://api.dexscreener.com/latest/dex/pairs"
    r = requests.get(url)

    if r.status_code != 200:
        return []

    return r.json().get("pairs", [])


def scan():

    pairs = get_pairs()

    for p in pairs:

        pair = p.get("pairAddress")

        if pair in ALERTED:
            continue

        price = float(p.get("priceUsd", 0))
        liquidity = p.get("liquidity", {}).get("usd", 0)

        volume5m = p.get("volume", {}).get("m5", 0)
        change5m = p.get("priceChange", {}).get("m5", 0)
        trades5m = p.get("txns", {}).get("m5", {}).get("buys", 0) + p.get("txns", {}).get("m5", {}).get("sells", 0)

        if price == 0:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        if volume5m < MIN_VOLUME_5M:
            continue

        if change5m < MIN_PRICE_CHANGE_5M:
            continue

        if trades5m < MIN_TRADES_5M:
            continue

        token = p.get("baseToken", {}).get("symbol", "UNKNOWN")
        chain = p.get("chainId")
        dex = p.get("dexId")

        msg = f"""
🚀 MICRO CAP RUNNER

Token: {token}
Price: ${price}

5m Change: {round(change5m,2)}%
5m Volume: ${volume5m}
Liquidity: ${liquidity}

Chain: {chain}
DEX: {dex}

Potential early momentum
"""

        print(msg)

        send_telegram(msg)

        ALERTED.add(pair)


def main():

    send_telegram("🚀 Micro Cap Runner Bot Started")

    while True:

        try:

            scan()

        except Exception as e:
            print("Error:", e)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
