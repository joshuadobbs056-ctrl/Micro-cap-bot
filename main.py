import os
import time
import requests
from collections import deque

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))

# Filtering Thresholds
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", 40000))
MAX_LIQUIDITY = int(os.getenv("MAX_LIQUIDITY", 500000))
MIN_VOLUME_5M = int(os.getenv("MIN_VOLUME_5M", 25000))
MIN_PRICE_CHANGE_1M = float(os.getenv("MIN_PRICE_CHANGE_1M", 2.0))
MIN_PRICE_CHANGE_5M = float(os.getenv("MIN_PRICE_CHANGE_5M", 6.0))
MIN_TRADES_5M = int(os.getenv("MIN_TRADES_5M", 25))
MIN_BUY_RATIO = float(os.getenv("MIN_BUY_RATIO", 0.60)) # 60% of txns must be buys
MIN_FDV = int(os.getenv("MIN_FDV", 200000))
MAX_FDV = int(os.getenv("MAX_FDV", 20000000))

# Anti-Spam / Tracking
alerted_tokens = {} # Stores {address: last_alert_price}
watchlist = deque(maxlen=5)

# Search Keywords
queries = ["usd","sol","eth","bnb","pepe","doge","ai","elon","pump","rocket"]

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Telegram not configured. Log: {msg[:50]}...")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_pairs():
    all_pairs = []
    seen = set()
    for q in queries:
        try:
            # Note: Search endpoint is often cached/delayed
            url = f"https://api.dexscreener.com/latest/dex/search/?q={q}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                for p in data.get("pairs", []):
                    addr = p.get("pairAddress")
                    if addr and addr not in seen:
                        seen.add(addr)
                        all_pairs.append(p)
            time.sleep(0.5) # Avoid hitting rate limits
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

    # Basic threshold checks
    if not (MIN_LIQUIDITY <= liq <= MAX_LIQUIDITY): return False
    if vol5 < MIN_VOLUME_5M: return False
    if change1 < MIN_PRICE_CHANGE_1M: return False
    if change5 < MIN_PRICE_CHANGE_5M: return False
    if total_trades < MIN_TRADES_5M: return False
    
    # Corrected Buy Ratio: Buys should be > 60% of total 5m trades
    if total_trades > 0:
        if (buys / total_trades) < MIN_BUY_RATIO: return False
    
    if not (MIN_FDV <= fdv <= MAX_FDV): return False
    
    return True

def score_pair(pair):
    score = 0
    change1 = pair.get("priceChange", {}).get("m1", 0)
    change5 = pair.get("priceChange", {}).get("m5", 0)
    vol5 = pair.get("volume", {}).get("m5", 0)
    liq = pair.get("liquidity", {}).get("usd", 0)
    
    # Momentum Velocity: Is it accelerating right now?
    if change1 > (change5 * 0.5): score += 3 
    elif change1 > 3: score += 1
    
    # Volume/Liquidity Intensity
    if vol5 > liq: score += 4 # Massive relative volume
    elif vol5 > (liq * 0.5): score += 2
    
    return score

def run():
    print("Scanner started... looking for runners.")
    last_report = time.time()
    
    while True:
        pairs = get_pairs()
        found_this_run = 0

        for pair in pairs:
            if not passes_filters(pair):
                continue

            addr = pair.get("pairAddress")
            symbol = pair.get("baseToken", {}).get("symbol", "UNK")
            price = float(pair.get("priceUsd", 0))
            
            # Anti-Spam: Only alert if new or if price rose 10% since last alert
            last_p = alerted_tokens.get(addr, 0)
            if last_p > 0 and price < (last_p * 1.10):
                continue

            score = score_pair(pair)
            if score < 5: continue

            alerted_tokens[addr] = price
            found_this_run += 1
            
            # Formatting Alert
            change5 = pair.get("priceChange", {}).get("m5", 0)
            vol5 = pair.get("volume", {}).get("m5", 0)
            liq = pair.get("liquidity", {}).get("usd", 0)
            
            msg = (f"🚀 EXPLOSIVE RUNNER: {symbol}\n"
                   f"Price: ${price:.10f}\n"
                   f"5m Change: {change5}%\n"
                   f"5m Vol: ${int(vol5):,}\n"
                   f"Liq: ${int(liq):,}\n"
                   f"Score: {score}/10\n"
                   f"Link: https://dexscreener.com/search?q={addr}")
            
            send_telegram(msg)
            watchlist.appendleft(f"{symbol} | {change5}% | ${int(vol5)}")

        # Hourly status update or summary
        if time.time() - last_report > 3600:
            summary = "📊 1H Watchlist Summary:\n" + "\n".join(watchlist)
            send_telegram(summary)
            last_report = time.time()

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    run()
