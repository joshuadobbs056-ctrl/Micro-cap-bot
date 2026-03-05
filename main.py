import os
import time
import requests
from collections import deque

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))

MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", 40000))
MAX_LIQUIDITY = int(os.getenv("MAX_LIQUIDITY", 500000))

MIN_VOLUME_5M = int(os.getenv("MIN_VOLUME_5M", 25000))

MIN_PRICE_CHANGE_1M = float(os.getenv("MIN_PRICE_CHANGE_1M", 2))
MIN_PRICE_CHANGE_5M = float(os.getenv("MIN_PRICE_CHANGE_5M", 6))

MIN_TRADES_5M = int(os.getenv("MIN_TRADES_5M", 25))

MIN_BUY_RATIO = float(os.getenv("MIN_BUY_RATIO", 0.60))

MIN_FDV = int(os.getenv("MIN_FDV", 200000))
MAX_FDV = int(os.getenv("MAX_FDV", 20000000))

MAX_PAIR_AGE = int(os.getenv("MAX_PAIR_AGE", 86400))

queries = [
    "usd","sol","eth","bnb","usdc",
    "pepe","doge","inu","ai","cat",
    "elon","moon","pump","rocket"
]

watchlist = deque(maxlen=5)

alerts_sent = 0
pairs_scanned = 0
pairs_passed = 0


def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    })


def get_pairs():

    all_pairs = []
    seen = set()

    for q in queries:

        try:
            url = f"https://api.dexscreener.com/latest/dex/search/?q={q}"
            r = requests.get(url, timeout=10)
            data = r.json()

            for p in data.get("pairs", []):

                pair_id = p.get("pairAddress")

                if pair_id in seen:
                    continue

                seen.add(pair_id)
                all_pairs.append(p)

        except:
            pass

    return all_pairs


def score_pair(pair):

    score = 0

    change1 = pair.get("priceChange", {}).get("m1", 0)
    change5 = pair.get("priceChange", {}).get("m5", 0)

    vol5 = pair.get("volume", {}).get("m5", 0)

    liq = pair.get("liquidity", {}).get("usd", 0)

    buys = pair.get("txns", {}).get("m5", {}).get("buys", 0)
    sells = pair.get("txns", {}).get("m5", {}).get("sells", 0)

    if change1 > 3:
        score += 2

    if change5 > 10:
        score += 3

    if vol5 > liq * 0.4:
        score += 2

    if buys > sells * 1.5:
        score += 2

    if vol5 > MIN_VOLUME_5M * 2:
        score += 1

    return score


def passes_filters(pair):

    global pairs_passed

    liq = pair.get("liquidity", {}).get("usd", 0)
    vol5 = pair.get("volume", {}).get("m5", 0)

    change1 = pair.get("priceChange", {}).get("m1", 0)
    change5 = pair.get("priceChange", {}).get("m5", 0)

    fdv = pair.get("fdv", 0)

    buys = pair.get("txns", {}).get("m5", {}).get("buys", 0)
    sells = pair.get("txns", {}).get("m5", {}).get("sells", 0)

    trades = buys + sells

    if liq < MIN_LIQUIDITY or liq > MAX_LIQUIDITY:
        return False

    if vol5 < MIN_VOLUME_5M:
        return False

    if change1 < MIN_PRICE_CHANGE_1M:
        return False

    if change5 < MIN_PRICE_CHANGE_5M:
        return False

    if trades < MIN_TRADES_5M:
        return False

    if buys / max(sells,1) < MIN_BUY_RATIO:
        return False

    if fdv < MIN_FDV or fdv > MAX_FDV:
        return False

    pairs_passed += 1
    return True


def format_watchlist():

    if not watchlist:
        return "None"

    return "\n".join(watchlist)


def run():

    global alerts_sent, pairs_scanned, pairs_passed

    last_report = time.time()

    while True:

        pairs = get_pairs()

        pairs_scanned = len(pairs)
        pairs_passed = 0

        for pair in pairs:

            if not passes_filters(pair):
                continue

            score = score_pair(pair)

            if score < 6:
                continue

            symbol = pair.get("baseToken", {}).get("symbol", "UNK")

            price = pair.get("priceUsd", "?")

            change5 = pair.get("priceChange", {}).get("m5", 0)

            vol5 = pair.get("volume", {}).get("m5", 0)

            liq = pair.get("liquidity", {}).get("usd", 0)

            entry = f"{symbol}  |  {change5}%  |  Vol ${int(vol5)}"

            watchlist.appendleft(entry)

            msg = f"""
🚀 MICROCAP RUNNER DETECTED

Token: {symbol}
Price: ${price}

5m Change: {change5}%
5m Volume: ${int(vol5)}
Liquidity: ${int(liq)}

Momentum Score: {score}/10
"""

            send_telegram(msg)

            alerts_sent += 1

        if time.time() - last_report > 60:

            status = f"""
📊 SCANNER STATUS

Pairs scanned: {pairs_scanned}
Passed filters: {pairs_passed}
Alerts sent: {alerts_sent}

🔥 Recent Runner Candidates
{format_watchlist()}
"""

            send_telegram(status)

            last_report = time.time()

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run()
