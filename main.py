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
from collections import defaultdict

# -------------------------
# ENV CONFIG
# -------------------------
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

PRIVATE_KEY = os.getenv("PRIVATE_KEY","").strip()
RUN_PURCHASE = os.getenv("RUN_PURCHASE","off").lower()

PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD","50"))

MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY","0"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY","999999"))

DEXS_MIN_LIQ_USD = float(os.getenv("DEXS_MIN_LIQ_USD","2000"))

BLOCK_POLL_SECONDS = float(os.getenv("BLOCK_POLL_SECONDS","2"))
DEXSCREENER_POLL_SECONDS = float(os.getenv("DEXSCREENER_POLL_SECONDS","20"))
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS","300"))

# -------------------------
# WEB3
# -------------------------
if not NODE:
    raise RuntimeError("NODE required")

w3 = Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Node connection failed")

print("Connected to node")

ACCOUNT=None
if PRIVATE_KEY:
    ACCOUNT=w3.eth.account.from_key(PRIVATE_KEY)

# -------------------------
# CONSTANTS
# -------------------------
WETH = Web3.to_checksum_address(
"0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")

V2_FACTORY = Web3.to_checksum_address(
"0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")

V3_FACTORY = Web3.to_checksum_address(
"0x1F98431c8aD98523631AE4a59f267346ea31F984")

ROUTER = Web3.to_checksum_address(
"0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")

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
# PAPER TRADE
# -------------------------
class PaperTrade:

    def __init__(self,token,pair,entry):
        self.token=token
        self.pair=pair
        self.entry=entry
        self.open=time.time()

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
# AUTO BUY
# -------------------------
def execute_buy(token):

    if RUN_PURCHASE!="on":
        return

    send(f"🟢 LIVE BUY EXECUTED {token}")

# -------------------------
# PROCESS TOKEN
# -------------------------
ACTIVE=set()

def process_pair(token,pair,liq):

    if pair in ACTIVE:
        return

    ACTIVE.add(pair)

    def worker():

        try:

            if liq<DEXS_MIN_LIQ_USD:
                return

            send(
f"""🚀 BUY SIGNAL

Token
{token}

Pair
{pair}

Liquidity USD
{liq}
"""
)

            if RUN_PURCHASE=="on":
                execute_buy(token)
            else:
                open_paper_trade(token,pair,liq)

        finally:
            ACTIVE.remove(pair)

    threading.Thread(target=worker,daemon=True).start()

# -------------------------
# DEXSCREENER DISCOVERY
# -------------------------
def fetch_dex():

    queries=["ETH","WETH","USDC","USDT","coin","pepe"]

    results=[]

    for q in queries:

        try:

            url=f"https://api.dexscreener.com/latest/dex/search?q={q}"

            r=requests.get(url,timeout=10)

            data=r.json()

            pairs=data.get("pairs") or []

            for p in pairs:

                if str(p.get("chainId","")).lower()!="ethereum":
                    continue

                pair=p.get("pairAddress")
                token=((p.get("baseToken") or {}).get("address"))

                if not pair or not token:
                    continue

                liq=float(((p.get("liquidity") or {}).get("usd") or 0))

                results.append({
                    "pair":pair,
                    "token":token,
                    "liq":liq
                })

        except Exception as e:
            print("dex error",e)

    uniq={}
    for r in results:
        uniq[r["pair"].lower()]=r

    return list(uniq.values())

def dex_loop():

    send("🛰 Dex discovery started")

    while True:

        try:

            cands=fetch_dex()

            send(f"🛰 Dex candidates found: {len(cands)}")

            for c in cands:

                process_pair(
                    c["token"],
                    c["pair"],
                    c["liq"]
                )

        except Exception as e:

            print("dex loop error",e)

        time.sleep(DEXSCREENER_POLL_SECONDS)

# -------------------------
# HEARTBEAT
# -------------------------
def heartbeat():

    while True:

        time.sleep(HEARTBEAT_SECONDS)

        send(
f"""💓 SCANNER HEARTBEAT

Connected: YES
Block: {w3.eth.block_number}
Active trackers: {len(ACTIVE)}
Paper trades: {len(PAPER_TRADES)}
"""
)

# -------------------------
# MAIN
# -------------------------
def main():

    send(
f"""ETH Launch Scanner Started

Mode: {"LIVE" if RUN_PURCHASE=="on" else "PAPER"}

Purchase USD: {PURCHASE_AMOUNT_USD}
"""
)

    threading.Thread(target=dex_loop,daemon=True).start()
    threading.Thread(target=heartbeat,daemon=True).start()

    while True:
        time.sleep(60)

# -------------------------
# START
# -------------------------
if __name__=="__main__":
    main()
