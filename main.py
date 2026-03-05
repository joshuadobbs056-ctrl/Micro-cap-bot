import os
import time
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 15))

MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", 50000))
MIN_VOLUME_5M = int(os.getenv("MIN_VOLUME_5M", 25000))

MIN_PRICE_CHANGE_5M = float(os.getenv("MIN_PRICE_CHANGE_5M", 6))
MIN_PRICE_CHANGE_1M = float(os.getenv("MIN_PRICE_CHANGE_1M", 2))

MIN_TRADES_5M = int(os.getenv("MIN_TRADES_5M", 20))
MIN_BUY_RATIO = float(os.getenv("MIN_BUY_RATIO", 0.6))

MIN_FDV = int(os.getenv("MIN_FDV", 200000))
MAX_FDV = int(os.getenv("MAX_FDV", 20000000))

MIN_VOL_LIQ_RATIO = float(os.getenv("MIN_VOL_LIQ_RATIO", 0.5))

MAX_PAIR_AGE_MINUTES = int(os.getenv("MAX_PAIR_AGE_MINUTES", 180))

MIN_SCORE = int(os.getenv("MIN_SCORE", 6))

ALERTED = set()
last_status = 0


def send_telegram(msg):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        })
    except:
        pass


# FIXED MARKET FETCH FUNCTION
def get_pairs():

    url = "https://api.dexscreener.com/latest/dex/search/?q=usd"

    try:
        r = requests.get(url, timeout=10)

        if r.status_code != 200:
            return []

        data = r.json()

        return data.get("pairs", [])

    except Exception as e:
        print("Dexscreener fetch error:", e)
        return []


def calculate_score(change1m, change5m, buy_ratio, vol_liq_ratio, accumulation):

    score = 0

    if change1m > 3:
        score += 2

    if change5m > 8:
        score += 2

    if buy_ratio > 0.7:
        score += 2

    if vol_liq_ratio > 0.7:
        score += 2

    if accumulation:
        score += 2

    return score


def scan():

    pairs = get_pairs()

    print(f"Pairs fetched: {len(pairs)}")

    scanned = 0
    passed = 0
    alerts = 0

    for p in pairs:

        scanned += 1

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

        pair_created = p.get("pairCreatedAt", 0)

        if price == 0:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        if volume5m < MIN_VOLUME_5M:
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

        if pair_created:

            age_minutes = (time.time()*1000 - pair_created) / 60000

            if age_minutes > MAX_PAIR_AGE_MINUTES:
                continue

        accumulation = False

        if change5m < 3 and buy_ratio > 0.65 and volume5m > MIN_VOLUME_5M:
            accumulation = True

        score = calculate_score(change1m, change5m, buy_ratio, vol_liq_ratio, accumulation)

        if score < MIN_SCORE:
            continue

        passed += 1

        token = p.get("baseToken", {}).get("symbol", "UNKNOWN")
        chain = p.get("chainId")
        dex = p.get("dexId")

        msg = f"""
🚀 MICRO CAP RUNNER

Token: {token}
Score: {score}/10

1m Change: {round(change1m,2)}%
5m Change: {round(change5m,2)}%

5m Volume: ${volume5m}
Liquidity: ${liquidity}

Volume/Liquidity: {round(vol_liq_ratio,2)}
Buy Pressure: {round(buy_ratio*100,1)}%

FDV: ${fdv}

Chain: {chain}
DEX: {dex}
"""

        print(msg)

        send_telegram(msg)

        ALERTED.add(pair)

        alerts += 1

    return scanned, passed, alerts


def status(scanned, passed, alerts):

    global last_status

    now = time.time()

    if now - last_status > 60:

        msg = f"""
📊 SCANNER STATUS

Pairs scanned: {scanned}
Passed filters: {passed}
Alerts sent: {alerts}

Scanner running normally
"""

        send_telegram(msg)

        last_status = now


def main():

    send_telegram("🚀 Micro Cap Runner Bot Started")

    while True:

        try:

            scanned, passed, alerts = scan()

            status(scanned, passed, alerts)

        except Exception as e:

            print("Error:", e)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
