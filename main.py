import os
import requests
import re
import time

# =========================
# CONFIG
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
GOPLUS_API = "https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={}"

# =========================
# TELEGRAM
# =========================
def send(msg):
    print(msg)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                timeout=10
            )
        except:
            pass

# =========================
# DOC ANALYSIS
# =========================
BULLISH_WORDS = [
    "launch", "mainnet", "bridge", "listing",
    "partnership", "integration", "roadmap",
    "tokenomics", "utility", "staking"
]

SCAM_WORDS = [
    "100x", "guaranteed", "profit",
    "no risk", "free money"
]

def score_text(text):
    text = text.lower()
    bullish = sum(word in text for word in BULLISH_WORDS)
    scam = sum(word in text for word in SCAM_WORDS)

    return bullish - (scam * 2)

def fetch_text(url):
    try:
        r = requests.get(url, timeout=10)
        return r.text.lower()
    except:
        return ""

# =========================
# GOPLUS CHECK
# =========================
def check_goplus(token):
    try:
        r = requests.get(GOPLUS_API.format(token), timeout=10)
        data = r.json()
        result = data["result"][token.lower()]

        honeypot = result.get("is_honeypot") == "1"
        cannot_sell = result.get("cannot_sell_all") == "1"

        buy_tax = float(result.get("buy_tax") or 0)
        sell_tax = float(result.get("sell_tax") or 0)

        return {
            "honeypot": honeypot,
            "cannot_sell": cannot_sell,
            "buy_tax": buy_tax,
            "sell_tax": sell_tax
        }
    except:
        return None

# =========================
# DEX CHECK
# =========================
def get_dex(token):
    try:
        r = requests.get(DEX_API.format(token), timeout=10)
        pairs = r.json().get("pairs", [])

        if not pairs:
            return None

        p = pairs[0]

        return {
            "price": float(p.get("priceUsd", 0)),
            "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
            "volume": float(p.get("volume", {}).get("m5", 0)),
            "buys": p.get("txns", {}).get("m5", {}).get("buys", 0),
            "sells": p.get("txns", {}).get("m5", {}).get("sells", 0),
            "url": p.get("url", "")
        }
    except:
        return None

# =========================
# MAIN ANALYSIS
# =========================
def analyze_token(token):
    send(f"🔎 SCANNING TOKEN\n\n{token}")

    g = check_goplus(token)
    d = get_dex(token)

    if not d:
        send("❌ No Dex data")
        return

    score = 0

    # SECURITY
    if g:
        if g["honeypot"]:
            score -= 5
        else:
            score += 2

        if g["cannot_sell"]:
            score -= 5

        if g["sell_tax"] <= 10:
            score += 1
        else:
            score -= 2

    # MARKET
    if d["liquidity"] > 10000:
        score += 2
    if d["volume"] > 1000:
        score += 2
    if d["buys"] > d["sells"]:
        score += 1

    # DOC (basic — using Dex link page text)
    text = fetch_text(d["url"])
    doc_score = score_text(text)
    score += doc_score

    # =========================
    # FINAL REPORT
    # =========================
    verdict = "🟢 STRONG" if score >= 5 else "🟡 MID" if score >= 2 else "🔴 RISKY"

    msg = (
        f"🚀 TOKEN REPORT\n\n"
        f"📌 CONTRACT\n{token}\n\n"
        f"💰 Price ${d['price']:.8f}\n"
        f"💧 Liquidity ${d['liquidity']:,.0f}\n"
        f"📊 Volume 5m ${d['volume']:,.0f}\n"
        f"🟢 Buys {d['buys']} | 🔴 Sells {d['sells']}\n\n"
        f"🛡️ SECURITY\n"
        f"Honeypot: {g['honeypot'] if g else 'unknown'}\n"
        f"Sell Tax: {g['sell_tax'] if g else 'unknown'}%\n\n"
        f"🧠 DOC SCORE {doc_score}\n"
        f"⭐ FINAL SCORE {score}\n\n"
        f"{verdict}\n\n"
        f"🔗 {d['url']}"
    )

    send(msg)

# =========================
# RUN
# =========================
if __name__ == "__main__":
    while True:
        token = input("Enter token contract: ").strip()
        if token:
            analyze_token(token)
