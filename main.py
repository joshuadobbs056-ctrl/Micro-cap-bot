import subprocess
import sys

def install(package: str):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import requests
except:
    install("requests")
    import requests

try:
    from web3 import Web3
except:
    install("web3")
    from web3 import Web3

import os
import time
import threading
from collections import defaultdict, deque

# -------------------------
# ENV CONFIG
# -------------------------
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

RUN_PURCHASE = os.getenv("RUN_PURCHASE","off").lower()
PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD","50"))

MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY","0"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY","999999"))

TRACK_SECONDS = int(os.getenv("TRACK_SECONDS","30"))
PAIR_POLL_SECONDS = float(os.getenv("PAIR_POLL_SECONDS","1.5"))
BLOCK_POLL_SECONDS = float(os.getenv("BLOCK_POLL_SECONDS","2"))
DEXSCREENER_POLL_SECONDS = float(os.getenv("DEXSCREENER_POLL_SECONDS","20"))

MONEY_MIN_BUYS = int(os.getenv("MONEY_MIN_BUYS","1"))
MONEY_MIN_UNIQUE_BUYERS = int(os.getenv("MONEY_MIN_UNIQUE_BUYERS","1"))
MONEY_MIN_BUY_ETH = float(os.getenv("MONEY_MIN_BUY_ETH","0.03"))
MONEY_MIN_BUYER_VELOCITY = float(os.getenv("MONEY_MIN_BUYER_VELOCITY","0.2"))
MAX_TOP_BUYER_SHARE = float(os.getenv("MAX_TOP_BUYER_SHARE","0.95"))

DEXS_MIN_LIQ_USD = float(os.getenv("DEXS_MIN_LIQ_USD","3000"))
DEXS_MIN_BUYS_5M = int(os.getenv("DEXS_MIN_BUYS_5M","1"))

SEND_STARTUP_HEARTBEAT = True
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS","300"))

ENABLE_UNISWAP_V2 = True
ENABLE_UNISWAP_V3 = True
ENABLE_DEXSCREENER_DISCOVERY = True

DEBUG_DEX = True

# -------------------------
# WEB3
# -------------------------
if not NODE:
    raise RuntimeError("NODE required")

w3 = Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Node connection failed")

print("Connected to node")

# -------------------------
# CONSTANTS
# -------------------------
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")

UNISWAP_V2_FACTORY = Web3.to_checksum_address(
"0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")

UNISWAP_V3_FACTORY = Web3.to_checksum_address(
"0x1F98431c8aD98523631AE4a59f267346ea31F984")

# -------------------------
# TELEGRAM
# -------------------------
def send(msg):

    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id":CHAT_ID,"text":msg},
                timeout=10
            )
        except:
            pass

    print(msg)

# -------------------------
# HELPERS
# -------------------------
def now_ts():
    return time.time()

def safe_float(v):
    try:
        return float(v)
    except:
        return 0

# -------------------------
# DEXSCREENER DISCOVERY
# -------------------------
def fetch_dexscreener_candidates():

    queries = [
        "ETH",
        "WETH",
        "USDC",
        "USDT",
        "coin",
        "pepe",
        "doge"
    ]

    results=[]

    for q in queries:

        try:

            url=f"https://api.dexscreener.com/latest/dex/search?q={q}"

            if DEBUG_DEX:
                print("Calling DexScreener search API:",url)

            r=requests.get(url,timeout=10)
            data=r.json()

            pairs=data.get("pairs") or []

            if DEBUG_DEX:
                print("DexScreener pair count:",len(pairs))

            for p in pairs:

                if str(p.get("chainId","")).lower()!="ethereum":
                    continue

                pair=p.get("pairAddress")
                token=((p.get("baseToken") or {}).get("address"))

                if not pair or not token:
                    continue

                liq_usd=safe_float(
                    ((p.get("liquidity") or {}).get("usd"))
                )

                buys_5m=safe_float(
                    (((p.get("txns") or {}).get("m5") or {}).get("buys"))
                )

                results.append({
                    "pair":pair,
                    "token":token,
                    "liq_usd":liq_usd,
                    "buys_5m":buys_5m
                })

        except Exception as e:
            print("Dex error:",e)

    # remove duplicates
    unique={}
    for r in results:
        unique[r["pair"].lower()]=r

    return list(unique.values())

# -------------------------
# PAPER TRADE
# -------------------------
class PaperTrade:

    def __init__(self,token,pair,liq):
        self.token=token
        self.pair=pair
        self.entry=liq
        self.open=now_ts()

PAPER_TRADES={}

def open_paper_trade(token,pair,liq):

    PAPER_TRADES[token]=PaperTrade(token,pair,liq)

    send(
f"""🧪 PAPER TRADE OPENED

Token
{token}

Pair
{pair}

Entry Liquidity
{liq}
"""
)

# -------------------------
# TRACK TOKEN
# -------------------------
ACTIVE_PAIRS=set()

def process_pair(token,pair,liq_usd):

    if pair in ACTIVE_PAIRS:
        return

    ACTIVE_PAIRS.add(pair)

    def worker():

        try:

            if liq_usd<DEXS_MIN_LIQ_USD:
                return

            send(
f"""🧪 WOULD BUY

Token
{token}

Pair
{pair}

Liquidity USD
{liq_usd}
"""
)

            open_paper_trade(token,pair,liq_usd)

        finally:

            ACTIVE_PAIRS.remove(pair)

    threading.Thread(target=worker,daemon=True).start()

# -------------------------
# DEX DISCOVERY LOOP
# -------------------------
def dexscreener_discovery_loop():

    send("🛰 Dex discovery started")

    while True:

        try:

            candidates=fetch_dexscreener_candidates()

            send(f"🛰 Dex candidates found: {len(candidates)}")

            for c in candidates:

                process_pair(
                    c["token"],
                    c["pair"],
                    c["liq_usd"]
                )

        except Exception as e:

            print("dex discovery error",e)

        time.sleep(DEXSCREENER_POLL_SECONDS)

# -------------------------
# HEARTBEAT
# -------------------------
def heartbeat_loop():

    while True:

        time.sleep(HEARTBEAT_SECONDS)

        send(
f"""💓 SCANNER HEARTBEAT

Connected: YES
Current block: {w3.eth.block_number}
Active pair trackers: {len(ACTIVE_PAIRS)}
Open paper trades: {len(PAPER_TRADES)}
"""
)

# -------------------------
# MAIN
# -------------------------
def main():

    send(
f"""ETH Scanner Started

Purchase Mode: PAPER
Purchase USD: {PURCHASE_AMOUNT_USD}
"""
)

    threading.Thread(
        target=heartbeat_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=dexscreener_discovery_loop,
        daemon=True
    ).start()

    while True:
        time.sleep(60)

# -------------------------
# START
# -------------------------
if __name__=="__main__":
    main()
