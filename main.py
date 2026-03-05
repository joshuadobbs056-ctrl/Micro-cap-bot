import os
import time
import requests
from collections import deque, defaultdict

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))

# Microcap Filters
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", 15000))
MAX_LIQUIDITY = int(os.getenv("MAX_LIQUIDITY", 600000))

MIN_VOLUME_5M = int(os.getenv("MIN_VOLUME_5M", 8000))

MIN_TRADES_5M = int(os.getenv("MIN_TRADES_5M", 10))
MIN_BUY_RATIO = float(os.getenv("MIN_BUY_RATIO", 0.55))

MIN_FDV = int(os.getenv("MIN_FDV", 60000))
MAX_FDV = int(os.getenv("MAX_FDV", 15000000))

# --- Consolidation Detection ---
CONSOLIDATION_DAYS = 14
MAX_RANGE_PERCENT = 12
MIN_HISTORY_POINTS = 120

# --- MACD Approximation ---
MACD_LOOKBACK = 30
MACD_THRESHOLD = 0.003

# --- Tracking ---
alerted_tokens = {}
watchlist = deque(maxlen=5)
price_history = defaultdict(lambda: deque(maxlen=2000))

queries = [
"usd","sol","eth","bnb","base",
"pepe","doge","inu","cat","mog",
"ai","elon","pump","rocket","moon",
"wojak","chad","based","meme"
]

scan_count = 0


def send_telegram(msg):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "disable_web_page_preview": True
        }, timeout=10)

    except:
        pass


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

            time.sleep(0.25)

        except:
            continue

    return all_pairs


def passes_filters(pair):

    liq = pair.get("liquidity", {}).get("usd", 0)
    vol5 = pair.get("volume", {}).get("m5", 0)
    fdv = pair.get("fdv", 0)

    txns = pair.get("txns", {}).get("m5", {})
    buys = txns.get("buys", 0)
    sells = txns.get("sells", 0)

    trades = buys + sells

    if not (MIN_LIQUIDITY <= liq <= MAX_LIQUIDITY):
        return False

    if vol5 < MIN_VOLUME_5M:
        return False

    if trades < MIN_TRADES_5M:
        return False

    if trades > 0 and (buys / trades) < MIN_BUY_RATIO:
        return False

    if not (MIN_FDV <= fdv <= MAX_FDV):
        return False

    return True


def detect_consolidation(addr):

    prices = price_history[addr]

    if len(prices) < MIN_HISTORY_POINTS:
        return False

    high = max(prices)
    low = min(prices)

    if low == 0:
        return False

    range_pct = ((high - low) / low) * 100

    return range_pct <= MAX_RANGE_PERCENT


def macd_near_cross(addr):

    prices = list(price_history[addr])

    if len(prices) < MACD_LOOKBACK:
        return False

    short_avg = sum(prices[-12:]) / 12
    long_avg = sum(prices[-26:]) / 26

    diff = short_avg - long_avg

    return abs(diff) < MACD_THRESHOLD


def run():

    global scan_count

    print("Microcap consolidation scanner active...")

    while True:

        start = time.time()

        pairs = get_pairs()

        heads = len(pairs)
        setups = []

        for pair in pairs:

            if not passes_filters(pair):
                continue

            addr = pair.get("pairAddress")

            symbol = pair.get("baseToken", {}).get("symbol", "UNK")

            price = float(pair.get("priceUsd", 0))

            price_history[addr].append(price)

            if not detect_consolidation(addr):
                continue

            if not macd_near_cross(addr):
                continue

            last = alerted_tokens.get(addr, 0)

            if last and price < last * 1.05:
                continue

            alerted_tokens[addr] = price

            liq = pair.get("liquidity", {}).get("usd", 0)

            msg = (
                f"📈 CONSOLIDATION BREAKOUT SETUP\n"
                f"{symbol}\n"
                f"Price: ${price:.10f}\n"
                f"Liq: ${int(liq):,}\n"
                f"14D Range: Tight\n"
                f"MACD: Near Cross\n"
                f"https://dexscreener.com/search?q={addr}"
            )

            setups.append(msg)

        scan_count += 1

        if scan_count % 10 == 0:

            send_telegram(
                f"🔎 CONSOLIDATION SCAN\n"
                f"Pairs scanned: {heads}\n"
                f"Setups: {len(setups)}\n"
                f"Scanner healthy ✅"
            )

        for m in setups:
            send_telegram(m)

        elapsed = time.time() - start

        time.sleep(max(0, SCAN_INTERVAL - elapsed))


if __name__ == "__main__":
    run()
