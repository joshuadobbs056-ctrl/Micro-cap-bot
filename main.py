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

MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY","1.5"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY","40"))

TRACK_SECONDS = int(os.getenv("TRACK_SECONDS","60"))
PAIR_POLL_SECONDS = float(os.getenv("PAIR_POLL_SECONDS","1.5"))

MONEY_MIN_BUYS = int(os.getenv("MONEY_MIN_BUYS","3"))
MONEY_MIN_UNIQUE_BUYERS = int(os.getenv("MONEY_MIN_UNIQUE_BUYERS","3"))
MONEY_MIN_BUY_ETH = float(os.getenv("MONEY_MIN_BUY_ETH","0.4"))
MONEY_MIN_BUYER_VELOCITY = float(os.getenv("MONEY_MIN_BUYER_VELOCITY","1.5"))

MAX_TOP_BUYER_SHARE = float(os.getenv("MAX_TOP_BUYER_SHARE","0.60"))

BLOCK_POLL_SECONDS = float(os.getenv("BLOCK_POLL_SECONDS","2"))

# -------------------------
# WEB3
# -------------------------
if not NODE:
    raise RuntimeError("NODE required")

w3 = Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Node connection failed")

print("Connected to node")

ACCOUNT = None
if PRIVATE_KEY:
    ACCOUNT = w3.eth.account.from_key(PRIVATE_KEY)

# -------------------------
# CONSTANTS
# -------------------------
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")

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

    def __init__(self,token,name,symbol,entry_eth):
        self.token=token
        self.name=name
        self.symbol=symbol
        self.entry=entry_eth
        self.open=time.time()

PAPER_TRADES={}

def open_paper_trade(token,name,symbol,eth):

    PAPER_TRADES[token]=PaperTrade(token,name,symbol,eth)

    send(
f"""🧪 PAPER TRADE OPENED

{name} ({symbol})

Entry: {eth:.4f} ETH
"""
)

def close_paper_trade(token,exit_eth):

    trade=PAPER_TRADES.get(token)
    if not trade:
        return

    pnl=(exit_eth/trade.entry-1)*100

    send(
f"""🧪 PAPER TRADE CLOSED

{trade.name} ({trade.symbol})

Entry: {trade.entry:.4f}
Exit: {exit_eth:.4f}

PnL: {pnl:.2f}%"""
)

    del PAPER_TRADES[token]

# -------------------------
# SMART WALLET MEMORY
# -------------------------
SMART_WALLETS=set()

def remember_wallet(wallet,profit_pct):

    if profit_pct>200:
        SMART_WALLETS.add(wallet)

def wallet_is_smart(wallet):
    return wallet in SMART_WALLETS

# -------------------------
# ABIs
# -------------------------
FACTORY_ABI=[{
"name":"PairCreated",
"type":"event",
"inputs":[
{"indexed":True,"name":"token0","type":"address"},
{"indexed":True,"name":"token1","type":"address"},
{"indexed":False,"name":"pair","type":"address"}
]
}]

PAIR_ABI=[
{"name":"getReserves","outputs":[
{"type":"uint112"},
{"type":"uint112"},
{"type":"uint32"}],
"inputs":[],
"stateMutability":"view",
"type":"function"
},
{"name":"token0","outputs":[{"type":"address"}],"inputs":[],"stateMutability":"view","type":"function"},
{"name":"token1","outputs":[{"type":"address"}],"inputs":[],"stateMutability":"view","type":"function"}
]

ERC20_ABI=[
{"name":"name","outputs":[{"type":"string"}],"inputs":[],"stateMutability":"view","type":"function"},
{"name":"symbol","outputs":[{"type":"string"}],"inputs":[],"stateMutability":"view","type":"function"}
]

factory=w3.eth.contract(address=FACTORY,abi=FACTORY_ABI)

# -------------------------
# HELPERS
# -------------------------
def get_pair(pair):
    return w3.eth.contract(address=pair,abi=PAIR_ABI)

def get_liquidity(pair):

    try:
        c=get_pair(pair)
        r=c.functions.getReserves().call()
        t0=c.functions.token0().call()
        t1=c.functions.token1().call()

        if t0.lower()==WETH.lower():
            return float(w3.from_wei(r[0],"ether"))

        if t1.lower()==WETH.lower():
            return float(w3.from_wei(r[1],"ether"))

    except:
        pass

    return 0

def token_info(token):

    try:
        c=w3.eth.contract(address=token,abi=ERC20_ABI)
        return c.functions.name().call(),c.functions.symbol().call()
    except:
        return "Unknown","UNK"

# -------------------------
# TRACK TOKEN
# -------------------------
ACTIVE_PAIRS=set()

def process_pair(token,pair):

    if pair in ACTIVE_PAIRS:
        return

    ACTIVE_PAIRS.add(pair)

    def worker():

        try:

            liquidity=get_liquidity(pair)

            if liquidity<MIN_ETH_LIQUIDITY or liquidity>MAX_ETH_LIQUIDITY:
                return

            name,symbol=token_info(token)

            buyers=set()
            buyer_counts=defaultdict(int)
            buy_eth=0
            buy_count=0

            start=time.time()

            while time.time()-start<TRACK_SECONDS:

                # simplified scan placeholder
                time.sleep(PAIR_POLL_SECONDS)

            unique=len(buyers)

            velocity=unique/(TRACK_SECONDS/60)

            if buy_count<MONEY_MIN_BUYS:
                return

            if unique<MONEY_MIN_UNIQUE_BUYERS:
                return

            if buy_eth<MONEY_MIN_BUY_ETH:
                return

            if velocity<MONEY_MIN_BUYER_VELOCITY:
                return

            smart_detected=False
            for w in buyers:
                if wallet_is_smart(w):
                    smart_detected=True

            mode="🧪 WOULD BUY" if RUN_PURCHASE!="on" else "🟢 BUY"

            send(
f"""{mode}

{name} ({symbol})

Liquidity: {liquidity:.2f} ETH
Unique buyers: {unique}
Velocity: {velocity:.2f}/min
Smart wallets: {"YES" if smart_detected else "NO"}

Token
{token}

Pair
{pair}
"""
)

            if RUN_PURCHASE!="on":
                open_paper_trade(token,name,symbol,liquidity)

        finally:
            ACTIVE_PAIRS.remove(pair)

    threading.Thread(target=worker,daemon=True).start()

# -------------------------
# HANDLE EVENT
# -------------------------
def handle_event(e):

    t0=e["args"]["token0"]
    t1=e["args"]["token1"]
    pair=e["args"]["pair"]

    token=None

    if t0.lower()==WETH.lower():
        token=t1

    if t1.lower()==WETH.lower():
        token=t0

    if token:
        process_pair(
            Web3.to_checksum_address(token),
            Web3.to_checksum_address(pair)
        )

# -------------------------
# MAIN LOOP
# -------------------------
def main():

    send(
f"""ETH Scanner Started

Purchase Mode: {"LIVE" if RUN_PURCHASE=="on" else "PAPER"}

Purchase USD: {PURCHASE_AMOUNT_USD}
"""
)

    last_block=w3.eth.block_number

    while True:

        try:

            block=w3.eth.block_number

            if block>last_block:

                events=factory.events.PairCreated.get_logs(
                    from_block=last_block+1,
                    to_block=block
                )

                for e in events:
                    handle_event(e)

                last_block=block

            time.sleep(BLOCK_POLL_SECONDS)

        except Exception as e:
            print("loop error",e)
            time.sleep(5)

# -------------------------
# START
# -------------------------
if __name__=="__main__":
    main()
