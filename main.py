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
elif NODE.startswith("ws://"):
    NODE = NODE.replace("ws://", "http://", 1)

# =========================
# WEB3
# =========================
w3 = Web3(Web3.HTTPProvider(NODE, request_kwargs={"timeout": 30}))

if not w3.is_connected():
    raise RuntimeError("Node failed")

V2_FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")

# =========================
# HTTP SESSION
# =========================
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

# =========================
# TELEGRAM
# =========================
def send(msg: str):
    print(msg)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            SESSION.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                timeout=10,
            )
        except Exception as e:
            print("telegram error:", e)

# =========================
# GOPLUS
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
# DEX
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
            "symbol": (p.get("baseToken") or {}).get("symbol", "UNK"),
            "websites": (p.get("info") or {}).get("websites", []) or [],
            "socials": (p.get("info") or {}).get("socials", []) or [],
        }
    except Exception:
        return None

# =========================
# DOCUMENT + NARRATIVE SCAN
# =========================
def analyze_docs(urls):
    score = 0
    summary = "No site"
    narrative = "Unknown"

    if not urls:
        return 0, summary, narrative

    try:
        url = urls[0].get("url")
        if not url:
            return 0, summary, narrative

        r = SESSION.get(url, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ").lower()

        # narrative detection
        if "artificial intelligence" in text or " ai " in f" {text} ":
            narrative = "AI"
            score += 2
        elif "game" in text or "gaming" in text:
            narrative = "Gaming"
            score += 1
        elif "defi" in text or "decentralized finance" in text:
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
        if "audit" in text or "audited" in text:
            score += 2
        if "team" in text:
            score += 1
        if "utility" in text:
            score += 1

        # scam-ish language
        if "guaranteed" in text or "100x" in text or "1000x" in text:
            score -= 3
        if "no risk" in text or "free money" in text:
            score -= 3

        summary = "Strong" if score >= 4 else "Basic"

        return score, summary, narrative

    except Exception:
        return 0, "Failed", "Unknown"

# =========================
# SOCIAL CHECK
# =========================
def analyze_socials(socials):
    score = 0
    found = []

    for s in socials:
        url = (s.get("url") or "").lower()
        if "twitter.com" in url or "x.com" in url:
            score += 1
            if "Twitter/X" not in found:
                found.append("Twitter/X")
        if "t.me" in url or "telegram" in url:
            score += 1
            if "Telegram" not in found:
                found.append("Telegram")
        if "discord" in url:
            score += 1
            if "Discord" not in found:
                found.append("Discord")

    return score, ", ".join(found) if found else "None"

# =========================
# ANALYZE TOKEN
# =========================
def analyze_token(token: str):
    try:
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
            else:
                score -= 5

            if g["sell_tax"] <= 10:
                score += 1
            else:
                score -= 2

            if g["buy_tax"] <= 15:
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

        # DOCS / SOCIALS
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
            f"🚫 Cannot Sell: {g['cannot_sell'] if g else 'unknown'}\n"
            f"Buy Tax: {g['buy_tax'] if g else '?'}%\n"
            f"Sell Tax: {g['sell_tax'] if g else '?'}%\n\n"
            f"🧠 Narrative: {narrative}\n"
            f"🌐 Docs: {doc_summary}\n"
            f"🐦 Socials: {social_summary}\n\n"
            f"⭐ SCORE {score}\n"
            f"{verdict}\n\n"
            f"{d['url']}"
        )

        send(msg)

    except Exception as e:
        print("analyze_token error:", e)

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

def get_event_logs(event_obj, start_block, end_block):
    try:
        return event_obj.get_logs(from_block=start_block, to_block=end_block)
    except TypeError:
        return event_obj.get_logs(fromBlock=start_block, toBlock=end_block)

def fetch_logs(start, end):
    logs = []
    while start <= end:
        chunk_end = min(start + MAX_LOG_RANGE - 1, end)

        try:
            chunk = get_event_logs(v2.events.PairCreated, start, chunk_end)
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
