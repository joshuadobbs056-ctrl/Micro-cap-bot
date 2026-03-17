import os
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# =========================
# CONFIG
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "5000"))
MIN_VOLUME_5M = float(os.getenv("MIN_VOLUME_5M", "500"))

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"

DOC_SOURCES = [
    "https://coinmarketcap.com/new/",
    "https://www.coingecko.com/en/new-cryptocurrencies"
]

# =========================
# REGEX
# =========================
CONTRACT_REGEX = re.compile(r"0x[a-fA-F0-9]{40}")

# =========================
# SIGNAL WORDS
# =========================
BULLISH_WORDS = [
    "launch", "mainnet", "bridge", "listing",
    "partnership", "integration", "roadmap",
    "tokenomics", "staking", "utility"
]

BEARISH_WORDS = [
    "100x", "guaranteed", "profit",
    "no risk", "free money", "instant gains"
]

# =========================
# TELEGRAM
# =========================
def send(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)

# =========================
# FETCH TEXT
# =========================
def fetch_text(url):
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        return soup.get_text(" ").lower()
    except:
        return ""

# =========================
# SCORE DOCUMENT
# =========================
def score_document(text):
    bullish = sum(word in text for word in BULLISH_WORDS)
    bearish = sum(word in text for word in BEARISH_WORDS)

    contracts = CONTRACT_REGEX.findall(text)
    has_contract = len(contracts) > 0

    score = bullish * 1.5
    score -= bearish * 2

    if has_contract:
        score += 2

    return score, contracts

# =========================
# DEX CHECK
# =========================
def get_dex_data(contract):
    try:
        r = requests.get(DEX_API.format(contract), timeout=10)
        data = r.json()

        if "pairs" not in data or not data["pairs"]:
            return None

        pair = data["pairs"][0]

        return {
            "price": float(pair.get("priceUsd", 0)),
            "liquidity": float(pair.get("liquidity", {}).get("usd", 0)),
            "volume": float(pair.get("volume", {}).get("m5", 0)),
        }

    except:
        return None

# =========================
# MAIN LOOP
# =========================
def scanner():
    print("🚀 Doc + Dex Scanner Running...")

    while True:
        try:
            for url in DOC_SOURCES:
                text = fetch_text(url)

                if not text:
                    continue

                score, contracts = score_document(text)

                if score < 3:
                    continue

                for contract in contracts:
                    dex = get_dex_data(contract)
                    if not dex:
                        continue

                    if dex["liquidity"] < MIN_LIQUIDITY:
                        continue

                    if dex["volume"] < MIN_VOLUME_5M:
                        continue

                    send(
                        f"🔥 BULLISH DOC SIGNAL\n\n"
                        f"Score: {score:.2f}\n"
                        f"Contract: {contract}\n"
                        f"Liquidity: ${dex['liquidity']:.0f}\n"
                        f"5m Volume: ${dex['volume']:.0f}\n"
                        f"Price: ${dex['price']:.8f}"
                    )

        except Exception as e:
            print("Error:", e)

        time.sleep(CHECK_INTERVAL)

# =========================
# START
# =========================
if __name__ == "__main__":
    scanner()
