import os
import time
import threading
import requests
from web3 import Web3

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
    raise RuntimeError("NODE is missing")

if NODE.startswith("wss://"):
    NODE = NODE.replace("wss://", "https://", 1)
    print("Converted wss:// NODE to https:// for HTTPProvider")
elif NODE.startswith("ws://"):
    NODE = NODE.replace("ws://", "http://", 1)
    print("Converted ws:// NODE to http:// for HTTPProvider")

# =========================
# HTTP SESSION
# =========================
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

# =========================
# WEB3
# =========================
w3 = Web3(Web3.HTTPProvider(NODE, request_kwargs={"timeout": 30}))

if not w3.is_connected():
    raise RuntimeError("Failed to connect to node")

V2_FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")

# =========================
# TELEGRAM
# =========================
def send(msg: str):
    print(msg)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            SESSION.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": msg,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
        except Exception as e:
            print("Telegram send error:", e)

# =========================
# GOPLUS CHECK
# =========================
def check_goplus(token: str):
    try:
        r = SESSION.get(GOPLUS_API.format(token), timeout=10)
        data = r.json()
        result = (data.get("result") or {}).get(token.lower())

        if not result:
            return None

        return {
            "honeypot": result.get("is_honeypot") == "1",
            "cannot_sell": result.get("cannot_sell_all") == "1",
            "buy_tax": float(result.get("buy_tax") or 0),
            "sell_tax": float(result.get("sell_tax") or 0),
        }
    except Exception:
        return None

# =========================
# DEX DATA
# =========================
def get_dex(token: str):
    try:
        r = SESSION.get(DEX_API.format(token), timeout=10)
        pairs = r.json().get("pairs", [])

        if not pairs:
            return None

        p = pairs[0]

        return {
            "price": float(p.get("priceUsd", 0) or 0),
            "liquidity": float((p.get("liquidity") or {}).get("usd", 0) or 0),
            "volume": float((p.get("volume") or {}).get("m5", 0) or 0),
            "buys": int(((p.get("txns") or {}).get("m5") or {}).get("buys", 0) or 0),
            "sells": int(((p.get("txns") or {}).get("m5") or {}).get("sells", 0) or 0),
            "url": p.get("url", ""),
            "symbol": ((p.get("baseToken") or {}).get("symbol") or "UNK"),
        }
    except Exception:
        return None

# =========================
# ANALYZE TOKEN
# =========================
def analyze_token(token: str):
    try:
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
            f"🏷️ Symbol {d['symbol']}\n"
            f"💰 Price ${d['price']:.8f}\n"
            f"💧 Liquidity ${d['liquidity']:,.0f}\n"
            f"📊 Volume 5m ${d['volume']:,.0f}\n"
            f"🟢 Buys {d['buys']} | 🔴 Sells {d['sells']}\n\n"
            f"🛡️ Honeypot: {g['honeypot'] if g else 'unknown'}\n"
            f"🚫 Cannot Sell: {g['cannot_sell'] if g else 'unknown'}\n"
            f"🧾 Buy Tax: {g['buy_tax'] if g else 'unknown'}%\n"
            f"🧾 Sell Tax: {g['sell_tax'] if g else 'unknown'}%\n\n"
            f"⭐ SCORE {score}\n"
            f"{verdict}\n\n"
            f"🔗 {d['url']}"
        )

        send(msg)
    except Exception as e:
        print(f"analyze_token error for {token}: {e}")

# =========================
# EVENT LISTENER
# =========================
V2_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": False, "name": "pair", "type": "address"},
            {"indexed": False, "name": "", "type": "uint256"},
        ],
        "name": "PairCreated",
        "type": "event",
    }
]

v2 = w3.eth.contract(address=V2_FACTORY, abi=V2_ABI)
seen = set()

def fetch_paircreated_logs(start_block: int, end_block: int):
    logs = []
    current_start = start_block

    while current_start <= end_block:
        current_end = min(current_start + MAX_LOG_RANGE - 1, end_block)
        try:
            chunk_logs = v2.events.PairCreated.get_logs(
                from_block=current_start,
                to_block=current_end,
            )
            logs.extend(chunk_logs)
        except Exception as e:
            print(f"Log fetch error [{current_start}-{current_end}]: {e}")
        current_start = current_end + 1

    return logs

def listener():
    send("🚀 AUTO SCANNER STARTED")

    try:
        last_block = max(w3.eth.block_number - STARTUP_LOOKBACK_BLOCKS, 0)
    except Exception as e:
        raise RuntimeError(f"Could not read starting block: {e}")

    while True:
        try:
            current = w3.eth.block_number

            if current > last_block:
                logs = fetch_paircreated_logs(last_block + 1, current)

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
                            daemon=True,
                        ).start()

                last_block = current

            time.sleep(POLL_SECONDS)

        except Exception as e:
            print("Listener error:", e)
            time.sleep(5)

# =========================
# START
# =========================
if __name__ == "__main__":
    listener()
