import os
import time
import threading
import requests
from web3 import Web3
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================
NODE = os.getenv("NODE", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", os.getenv("CHAT_ID", "")).strip()

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
GOPLUS_API = "https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={}"

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "8000"))
MIN_VOLUME = float(os.getenv("MIN_VOLUME", "1000"))

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "5"))
STARTUP_LOOKBACK_BLOCKS = int(os.getenv("STARTUP_LOOKBACK_BLOCKS", "25"))
MAX_LOG_RANGE = int(os.getenv("MAX_LOG_RANGE", "10"))

# =========================
# NORMALIZE NODE
# =========================
if not NODE:
    raise RuntimeError("NODE missing")

if NODE.startswith("wss://"):
    NODE = NODE.replace("wss://", "https://", 1)

# =========================
# WEB3
# =========================
w3 = Web3(Web3.HTTPProvider(NODE, request_kwargs={"timeout": 30}))

if not w3.is_connected():
    raise RuntimeError("Node failed")

V2_FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")

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
                timeout=10,
            )
        except:
            pass

# =========================
# GOPLUS
# =========================
def check_goplus(token):
    try:
        r = requests.get(GOPLUS_API.format(token), timeout=10)
        data = r.json()["result"][token.lower()]

        return {
            "honeypot": data.get("is_honeypot") == "1",
            "cannot_sell": data.get("cannot_sell_all") == "1",
            "buy_tax": float(data.get("buy_tax") or 0),
            "sell_tax": float(data.get("sell_tax") or 0),
        }
    except:
        return None

# =========================
# DEX
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
            "url": p.get("url", ""),
            "symbol": p.get("baseToken", {}).get("symbol", "UNK"),
            "websites": p.get("info", {}).get("websites", []),
            "socials": p.get("info", {}).get("socials", []),
        }
    except:
        return None

# =========================
# DOCUMENT + NARRATIVE SCAN
# =========================
def analyze_docs(urls):
    score = 0
    narrative = "Unknown"

    if not urls:
        return 0, "No site", "Unknown"

    try:
        url = urls[0].get("url")
        r = requests.get(url, timeout=8)
        text = BeautifulSoup(r.text, "html.parser").get_text().lower()

        # narrative detection
        if "ai" in text:
            narrative = "AI"
            score += 2
        elif "game" in text:
            narrative = "Gaming"
            score += 1
        elif "defi" in text:
            narrative = "DeFi"
            score += 1
        elif "meme" in text:
            narrative = "Meme"
            score += 1

        # doc strength
        if "roadmap" in text:
            score += 1
        if "tokenomics" in text:
            score += 1
        if "whitepaper" in text:
            score += 2
        if "audit" in text:
            score += 2

        # scam detection
        if "guaranteed" in text or "100x" in text:
            score -= 3

        summary = "Strong" if score >= 4 else "Basic"

        return score, summary, narrative

    except:
        return 0, "Failed", "Unknown"

# =========================
# SOCIAL CHECK
# =========================
def analyze_socials(socials):
    score = 0
    found = []

    for s in socials:
        if "twitter" in s.get("url", ""):
            score += 1
            found.append("Twitter")
        if "telegram" in s.get("url", ""):
            score += 1
            found.append("Telegram")

    return score, ", ".join(found) if found else "None"

# =========================
# ANALYZE TOKEN
# =========================
def analyze_token(token):

    g = check_goplus(token)
    d = get_dex(token)

    if not d:
        return

    doc_score, doc_summary, narrative = analyze_docs(d["websites"])
    social_score, social_summary = analyze_socials(d["socials"])

    score = 0

    # SECURITY
    if g:
        if not g["honeypot"]:
            score += 2
        else:
            score -= 5

        if not g["cannot_sell"]:
            score += 1

        if g["sell_tax"] <= 10:
            score += 1

    # MARKET
    if d["liquidity"] > MIN_LIQUIDITY:
        score += 2
    if d["volume"] > MIN_VOLUME:
        score += 2
    if d["buys"] > d["sells"]:
        score += 1

    score += doc_score + social_score

    # verdict
    if score >= 8:
        verdict = "🔥 HIGH POTENTIAL"
    elif score >= 5:
        verdict = "💎 EARLY GEM"
    elif score >= 3:
        verdict = "⚠️ WATCH"
    else:
        verdict = "❌ AVOID"

    msg = (
        "🚀 NEW TOKEN DETECTED\n\n"
        "📌 CONTRACT\n"
        f"{token}\n\n"
        f"🏷️ {d['symbol']}\n"
        f"💰 ${d['price']:.8f}\n"
        f"💧 ${d['liquidity']:,.0f}\n"
        f"📊 Vol ${d['volume']:,.0f}\n"
        f"🟢 {d['buys']} | 🔴 {d['sells']}\n\n"
        f"🛡 Honeypot: {g['honeypot'] if g else 'unknown'}\n"
        f"Tax: {g['sell_tax'] if g else '?'}%\n\n"
        f"🧠 Narrative: {narrative}\n"
        f"🌐 Docs: {doc_summary}\n"
        f"🐦 Socials: {social_summary}\n\n"
        f"⭐ SCORE {score}\n"
        f"{verdict}\n\n"
        f"{d['url']}"
    )

    send(msg)

# =========================
# EVENT LISTENER
# =========================
V2_ABI = [{
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "token0", "type": "address"},
        {"indexed": True, "name": "token1", "type": "address"},
        {"indexed": False, "name": "pair", "type": "address"}
    ],
    "name": "PairCreated",
    "type": "event"
}]

v2 = w3.eth.contract(address=V2_FACTORY, abi=V2_ABI)
seen = set()

def fetch_logs(start, end):
    logs = []
    while start <= end:
        chunk_end = min(start + MAX_LOG_RANGE - 1, end)

        try:
            chunk = v2.events.PairCreated.get_logs(
                from_block=start,
                to_block=chunk_end
            )
            logs.extend(chunk)
        except Exception as e:
            print("log error", e)

        start = chunk_end + 1

    return logs

def listener():
    send("🚀 AI SCANNER STARTED")

    last_block = max(w3.eth.block_number - STARTUP_LOOKBACK_BLOCKS, 0)

    while True:
        try:
            current = w3.eth.block_number

            if current > last_block:
                logs = fetch_logs(last_block + 1, current)

                for e in logs:
                    t0 = e["args"]["token0"]
                    t1 = e["args"]["token1"]

                    token = None
                    if t0.lower() == WETH.lower():
                        token = t1
                    elif t1.lower() == WETH.lower():
                        token = t0

                    if token and token not in seen:
                        seen.add(token)
                        threading.Thread(
                            target=analyze_token,
                            args=(token,),
                            daemon=True
                        ).start()

                last_block = current

            time.sleep(POLL_SECONDS)

        except Exception as e:
            print("listener error:", e)
            time.sleep(5)

# =========================
# START
# =========================
if __name__ == "__main__":
    listener()
