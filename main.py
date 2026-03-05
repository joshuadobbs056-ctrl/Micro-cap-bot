import os
import time
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = 15

MIN_LIQUIDITY = 50000
MIN_VOLUME_5M = 25000
MIN_PRICE_CHANGE_5M = 6
MIN_PRICE_CHANGE_1M = 2
MIN_TRADES_5M = 20
MIN_BUY_RATIO = 0.6

MIN_FDV = 200000
MAX_FDV = 20000000

MIN_VOL_LIQ_RATIO = 0.5

ALERTED = set()


def send_telegram(msg):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        }
    )


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
        change1m = p.get("priceChange", {}).get("m1", 0)

        buys = p.get("txns", {}).get("m5", {}).get("buys", 0)
        sells = p.get("txns", {}).get("m5", {}).get("sells", 0)

        trades5m = buys + sells

        fdv = p.get("fdv", 0)

        if price == 0:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        if volume5m < MIN_VOLUME_5M:
            continue

        if change5m < MIN_PRICE_CHANGE_5M:
            continue

        if change1m < MIN_PRICE_CHANGE_1M:
            continue

        if trades5m < MIN_TRADES_5M:
            continue

        if fdv < MIN_FDV or fdv > MAX_FDV:
            continue

        if liquidity == 0:
            continue

        vol_liq_ratio = volume5m / liquidity

        if vol_liq_ratio < MIN_VOL_LIQ_RATIO:
            continue

        if trades5m == 0:
            continue

        buy_ratio = buys / trades5m

        if buy_ratio < MIN_BUY_RATIO:
            continue

        token = p.get("baseToken", {}).get("symbol", "UNKNOWN")
        chain = p.get("chainId")
        dex = p.get("dexId")

        msg = f"""
🚀 MICRO CAP RUNNER

Token: {token}
Price: ${price}

1m Change: {round(change1m,2)}%
5m Change: {round(change5m,2)}%

5m Volume: ${volume5m}
Liquidity: ${liquidity}

Volume/Liquidity: {round(vol_liq_ratio,2)}

Buy Pressure: {round(buy_ratio*100,1)}%

FDV: ${fdv}

Chain: {chain}
DEX: {dex}

Potential explosive momentum
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
