import os
import time
import requests
from collections import deque

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))

# Filtering Thresholds (Optimized for early runners)
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", 15000))
MAX_LIQUIDITY = int(os.getenv("MAX_LIQUIDITY", 600000))

MIN_VOLUME_5M = int(os.getenv("MIN_VOLUME_5M", 8000))

MIN_PRICE_CHANGE_1M = float(os.getenv("MIN_PRICE_CHANGE_1M", 1.2))
MIN_PRICE_CHANGE_5M = float(os.getenv("MIN_PRICE_CHANGE_5M", 4.0))

MIN_TRADES_5M = int(os.getenv("MIN_TRADES_5M", 12))
MIN_BUY_RATIO = float(os.getenv("MIN_BUY_RATIO", 0.65))

MIN_FDV = int(os.getenv("MIN_FDV", 60000))
MAX_FDV = int(os.getenv("MAX_FDV", 15000000))

# Anti-Spam / Tracking
alerted_tokens = {}
watchlist = deque(maxlen=5)

# Expanded search keywords for more discovery
queries = [
"usd","sol","eth","bnb","base",
"pepe","doge","inu","cat","mog",
"ai","elon","pump","rocket","moon",
"wojak","chad","based","meme"
]

scan_count = 0

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Log: {msg[:50]}...")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "disable_web_page_preview": True
        }, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_pairs():
    all_pairs = []
    seen = set()

    for q in queries:
        try:
            url = f"https://api.dexscreener.com/latest/dex/search/?q={q}"
            r = requests.get(url, timeout=10)

            if r.status_code == 200:
                data = r.json()

                for p in data.get("pairs", []):
                    addr = p.get("pairAddress")

                    if addr and addr not in seen:
                        seen.add(addr)
                        all_pairs.append(p)

            time.sleep(0.3)

        except:
            continue

    return all_pairs

def passes_filters(pair):

    liq = pair.get("liquidity", {}).get("usd", 0)
    vol5 = pair.get("volume", {}).get("m5", 0)
    change1 = pair.get("priceChange", {}).get("m1", 0)
    change5 = pair.get("priceChange", {}).get("m5", 0)
    fdv = pair.get("fdv", 0)

    txns = pair.get("txns", {}).get("m5", {})
    buys = txns.get("buys", 0)
    sells = txns.get("sells", 0)
    total_trades = buys + sells

    if not (MIN_LIQUIDITY <= liq <= MAX_LIQUIDITY):
        return False

    if vol5 < MIN_VOLUME_5M:
        return False

    if change1 < MIN_PRICE_CHANGE_1M:
        return False

    if change5 < MIN_PRICE_CHANGE_5M:
        return False

    if total_trades < MIN_TRADES_5M:
        return False

    if total_trades > 0:
        if (buys / total_trades) < MIN_BUY_RATIO:
            return False

    if not (MIN_FDV <= fdv <= MAX_FDV):
        return False

    return True

def score_pair(pair):

    score = 0

    change1 = pair.get("priceChange", {}).get("m1", 0)
    change5 = pair.get("priceChange", {}).get("m5", 0)

    vol5 = pair.get("volume", {}).get("m5", 0)
    liq = pair.get("liquidity", {}).get("usd", 0)

    if change1 > (change5 * 0.5):
        score += 3

    if vol5 > liq:
        score += 4

    elif vol5 > (liq * 0.5):
        score += 2

    return score

def run():

    global scan_count

    print("Scanner active. Sending heartbeats to Telegram...")

    while True:

        start_time = time.time()

        pairs = get_pairs()

        heads_scanned = len(pairs)
        potential_runners = 0
        run_alerts = []

        for pair in pairs:

            if not passes_filters(pair):
                continue

            addr = pair.get("pairAddress")

            symbol = pair.get("baseToken", {}).get("symbol", "UNK")

            price = float(pair.get("priceUsd", 0))

            last_p = alerted_tokens.get(addr, 0)

            if last_p > 0 and price < (last_p * 1.10):
                continue

            score = score_pair(pair)

            if score < 4:
                continue

            potential_runners += 1

            alerted_tokens[addr] = price

            change5 = pair.get("priceChange", {}).get("m5", 0)
            vol5 = pair.get("volume", {}).get("m5", 0)
            liq = pair.get("liquidity", {}).get("usd", 0)

            alert_msg = (
                f"🔥 RUNNER: {symbol}\n"
                f"Price: ${price:.10f}\n"
                f"5m: {change5}% | Vol: ${int(vol5):,}\n"
                f"Score: {score}/10\n"
                f"https://dexscreener.com/search?q={addr}"
            )

            run_alerts.append(alert_msg)

            watchlist.appendleft(f"{symbol} ({change5}%)")

        scan_count += 1

        if scan_count % 10 == 0:

            scan_report = (
                f"🔍 SCAN REPORT\n"
                f"━━━━━━━━━━━━━━\n"
                f"Heads Scanned: {heads_scanned}\n"
                f"Runners Found: {potential_runners}\n"
                f"Status: Healthy ✅"
            )

            send_telegram(scan_report)

        for alert in run_alerts:
            send_telegram(alert)

        elapsed = time.time() - start_time

        sleep_time = max(0, SCAN_INTERVAL - elapsed)

        time.sleep(sleep_time)

if __name__ == "__main__":
    run()
