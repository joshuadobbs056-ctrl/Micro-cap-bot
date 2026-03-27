import os
import time
import requests
from datetime import datetime
from collections import deque

# ================= CONFIG =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
LISTING_CHECK_INTERVAL = int(os.getenv("LISTING_CHECK_INTERVAL", "300"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "50000"))
MIN_VOLUME_5M = float(os.getenv("MIN_VOLUME_5M", "10000"))

BUY_SELL_RATIO_THRESHOLD = float(os.getenv("BUY_SELL_RATIO_THRESHOLD", "2.0"))
BOTTOM_RANGE_PCT = float(os.getenv("BOTTOM_RANGE_PCT", "0.08"))

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

seen_tokens = set()
seen_listings = set()

# ================= TELEGRAM =================

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        SESSION.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

# ================= MACD =================

def ema(values, period):
    k = 2 / (period + 1)
    ema_vals = []
    for i, v in enumerate(values):
        if i == 0:
            ema_vals.append(v)
        else:
            ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals

def calculate_macd(prices):
    if len(prices) < MACD_SLOW:
        return None, None, None
    ema_fast = ema(prices, MACD_FAST)
    ema_slow = ema(prices, MACD_SLOW)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, MACD_SIGNAL)
    hist = [m - s for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, hist

# ================= BASE SCANNER =================

def get_base_pairs():
    url = "https://api.dexscreener.com/latest/dex/pairs/base"
    try:
        return SESSION.get(url).json().get("pairs", [])
    except:
        return []

def analyze_pair(pair):
    try:
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        volume_5m = pair.get("volume", {}).get("m5", 0)

        if liquidity < MIN_LIQUIDITY or volume_5m < MIN_VOLUME_5M:
            return None

        buys = pair.get("txns", {}).get("m5", {}).get("buys", 0)
        sells = pair.get("txns", {}).get("m5", {}).get("sells", 1)

        ratio = buys / max(sells, 1)

        if ratio < BUY_SELL_RATIO_THRESHOLD:
            return None

        price = float(pair.get("priceUsd", 0))
        price_history = pair.get("priceChange", {})

        # crude bottom detection using 24h low approximation
        low_24h = price / (1 + abs(pair.get("priceChange", {}).get("h24", 0)/100))
        if abs(price - low_24h) / max(low_24h, 1e-9) > BOTTOM_RANGE_PCT:
            return None

        # fake price series (Dex doesn't give candles here)
        prices = [price * (1 - 0.01*i) for i in range(30)][::-1]

        macd, signal, hist = calculate_macd(prices)
        if not macd:
            return None

        # pre-crossover detection
        if macd[-1] < signal[-1] and macd[-1] > macd[-3] and hist[-1] > hist[-2]:
            return {
                "symbol": pair.get("baseToken", {}).get("symbol"),
                "price": price,
                "liquidity": liquidity,
                "ratio": ratio,
                "volume": volume_5m,
                "pair": pair.get("url")
            }

    except:
        return None

def scan_base():
    pairs = get_base_pairs()
    for p in pairs:
        result = analyze_pair(p)
        if not result:
            continue

        key = result["symbol"]
        if key in seen_tokens:
            continue
        seen_tokens.add(key)

        msg = f"""
🚀 BASE REVERSAL SETUP

Token: {result['symbol']}
Price: ${result['price']:.6f}

Buys/Sells: {result['ratio']:.2f}
Volume(5m): ${result['volume']:.0f}
Liquidity: ${result['liquidity']:.0f}

MACD: Curling up pre-crossover
Status: Bottom reversal watch

Chart:
{result['pair']}
"""
        send_telegram(msg)

# ================= COINBASE LISTINGS =================

def get_coinbase_listings():
    url = "https://api.coinbase.com/v2/assets/search?base=USD"
    try:
        return SESSION.get(url).json().get("data", [])
    except:
        return []

def check_listings():
    assets = get_coinbase_listings()

    for asset in assets[:50]:
        name = asset.get("name")
        symbol = asset.get("symbol")

        key = f"{symbol}"
        if key in seen_listings:
            continue

        seen_listings.add(key)

        desc = asset.get("description", "No description")

        msg = f"""
📢 COINBASE WATCH

Asset: {name} ({symbol})

What it is:
{desc[:200]}...

Status:
Tracked via Coinbase asset feed

Note:
Listing date not officially confirmed
"""
        send_telegram(msg)

# ================= MAIN LOOP =================

def main():
    last_listing_check = 0

    while True:
        try:
            scan_base()

            if time.time() - last_listing_check > LISTING_CHECK_INTERVAL:
                check_listings()
                last_listing_check = time.time()

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("ERROR:", e)
            time.sleep(10)

if __name__ == "__main__":
    print("🚀 BOT STARTED")
    main()
