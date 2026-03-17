import os
import time
import threading
import requests
from web3 import Web3

# =========================
# CONFIG
# =========================
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
GOPLUS_API = "https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={}"

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "8000"))
MIN_VOLUME = float(os.getenv("MIN_VOLUME", "1000"))

# =========================
# WEB3
# =========================
w3 = Web3(Web3.HTTPProvider(NODE))

V2_FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
V3_FACTORY = Web3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")
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
                timeout=10
            )
        except:
            pass

# =========================
# GOPLUS CHECK
# =========================
def check_goplus(token):
    try:
        r = requests.get(GOPLUS_API.format(token), timeout=10)
        data = r.json()["result"][token.lower()]

        return {
            "honeypot": data.get("is_honeypot") == "1",
            "cannot_sell": data.get("cannot_sell_all") == "1",
            "buy_tax": float(data.get("buy_tax") or 0),
            "sell_tax": float(data.get("sell_tax") or 0)
        }
    except:
        return None

# =========================
# DEX DATA
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
# ANALYZE TOKEN
# =========================
def analyze_token(token):

    g = check_goplus(token)
    d = get_dex(token)

    if not d:
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
    if d["liquidity"] > MIN_LIQUIDITY:
        score += 2
    if d["volume"] > MIN_VOLUME:
        score += 2
    if d["buys"] > d["sells"]:
        score += 1

    verdict = "🟢 STRONG" if score >= 5 else "🟡 MID" if score >= 2 else "🔴 RISKY"

    msg = (
        f"🚀 NEW TOKEN DETECTED\n\n"
        f"📌 CONTRACT\n{token}\n\n"
        f"💰 Price ${d['price']:.8f}\n"
        f"💧 Liquidity ${d['liquidity']:,.0f}\n"
        f"📊 Volume 5m ${d['volume']:,.0f}\n"
        f"🟢 Buys {d['buys']} | 🔴 Sells {d['sells']}\n\n"
        f"🛡️ Honeypot: {g['honeypot'] if g else 'unknown'}\n"
        f"Sell Tax: {g['sell_tax'] if g else 'unknown'}%\n\n"
        f"⭐ SCORE {score}\n"
        f"{verdict}\n\n"
        f"🔗 {d['url']}"
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

def listener():
    send("🚀 AUTO SCANNER STARTED")

    last_block = w3.eth.block_number

    while True:
        try:
            current = w3.eth.block_number

            if current > last_block:
                logs = v2.events.PairCreated.get_logs(
                    fromBlock=last_block + 1,
                    toBlock=current
                )

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
                        threading.Thread(target=analyze_token, args=(token,), daemon=True).start()

                last_block = current

            time.sleep(5)

        except Exception as e:
            print("Listener error:", e)
            time.sleep(5)

# =========================
# START
# =========================
if __name__ == "__main__":
    listener()
