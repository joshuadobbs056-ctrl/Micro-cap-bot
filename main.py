import subprocess
import sys

def install(package):
    subprocess.check_call([sys.executable,"-m","pip","install",package])

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

# -------------------------
# ENV VARIABLES
# -------------------------

NODE=os.getenv("NODE")
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN")
CHAT_ID=os.getenv("CHAT_ID")

RUN_PURCHASE=os.getenv("RUN_PURCHASE","off").lower()

START_BALANCE=float(os.getenv("START_BALANCE","2000"))
PURCHASE_AMOUNT_USD=float(os.getenv("PURCHASE_AMOUNT_USD","50"))

MAX_OPEN_TRADES=int(os.getenv("MAX_OPEN_TRADES","10"))

PORTFOLIO_UPDATE_SECONDS=int(os.getenv("PORTFOLIO_UPDATE_SECONDS","30"))

TAKE_PROFIT=float(os.getenv("TAKE_PROFIT","50"))
STOP_LOSS=float(os.getenv("STOP_LOSS","-30"))

ACCOUNT_CASH=START_BALANCE

# -------------------------
# WEB3 CONNECTION
# -------------------------

if not NODE:
    raise RuntimeError("NODE missing")

if NODE.startswith("ws"):
    w3=Web3(Web3.WebsocketProvider(NODE))
else:
    w3=Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Node connection failed")

print("Connected to node")

# -------------------------
# CONSTANTS
# -------------------------

WETH=Web3.to_checksum_address(
"0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2"
)

FACTORY=Web3.to_checksum_address(
"0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
)

FACTORY_ABI=[{
"name":"PairCreated",
"type":"event",
"inputs":[
{"indexed":True,"name":"token0","type":"address"},
{"indexed":True,"name":"token1","type":"address"},
{"indexed":False,"name":"pair","type":"address"}
]
}]

factory=w3.eth.contract(address=FACTORY,abi=FACTORY_ABI)

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
# PRICE DATA
# -------------------------

def get_pair_data(pair):

    try:

        url=f"https://api.dexscreener.com/latest/dex/pairs/ethereum/{pair}"

        r=requests.get(url,timeout=10)

        data=r.json()

        p=data.get("pair")

        if not p:
            return None

        return {
            "price":float(p.get("priceUsd") or 0)
        }

    except:
        return None

# -------------------------
# PAPER TRADE MODEL
# -------------------------

class PaperTrade:

    def __init__(self,token,pair,entry):

        self.token=token
        self.pair=pair
        self.entry_price=entry
        self.tokens=PURCHASE_AMOUNT_USD/entry
        self.open=time.time()

PAPER_TRADES={}

# -------------------------
# TRADE MONITOR
# -------------------------

def monitor_trade(token):

    global ACCOUNT_CASH

    trade=PAPER_TRADES[token]

    while True:

        time.sleep(60)

        data=get_pair_data(trade.pair)

        if not data:
            continue

        price=data["price"]

        value=trade.tokens*price

        pnl=((value-PURCHASE_AMOUNT_USD)/PURCHASE_AMOUNT_USD)*100

        send(
f"""📊 PAPER TRADE UPDATE

Token
{trade.token}

Entry ${trade.entry_price}

Current ${price}

Value ${value:.2f}

PnL {pnl:.2f}%"""
)

        if pnl>=TAKE_PROFIT or pnl<=STOP_LOSS:

            ACCOUNT_CASH+=value

            send(
f"""🧪 PAPER TRADE CLOSED

Token {trade.token}

Entry ${trade.entry_price}

Exit ${price}

Value ${value:.2f}

PnL {pnl:.2f}%"""
)

            del PAPER_TRADES[token]

            return

# -------------------------
# OPEN TRADE
# -------------------------

def open_trade(token,pair):

    global ACCOUNT_CASH

    if len(PAPER_TRADES)>=MAX_OPEN_TRADES:

        send("⚠️ Trade skipped — max trades reached")

        return

    if ACCOUNT_CASH<PURCHASE_AMOUNT_USD:

        send("⚠️ Trade skipped — insufficient balance")

        return

    data=get_pair_data(pair)

    if not data:
        return

    price=data["price"]

    trade=PaperTrade(token,pair,price)

    PAPER_TRADES[token]=trade

    ACCOUNT_CASH-=PURCHASE_AMOUNT_USD

    send(
f"""🧪 PAPER TRADE OPENED

Token {token}

Entry ${price}

Tokens {trade.tokens}

Open trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}"""
)

    threading.Thread(
        target=monitor_trade,
        args=(token,),
        daemon=True
    ).start()

# -------------------------
# PROCESS NEW LAUNCH
# -------------------------

ACTIVE=set()

def process_pair(token,pair):

    if pair in ACTIVE:
        return

    ACTIVE.add(pair)

    try:

        send(
f"""🚀 NEW LAUNCH DETECTED

Token {token}

Pair {pair}"""
)

        if RUN_PURCHASE=="on":
            send(f"🟢 LIVE BUY {token}")
        else:
            open_trade(token,pair)

    finally:

        ACTIVE.remove(pair)

# -------------------------
# EVENT LISTENER
# -------------------------

def event_listener():

    last_block=w3.eth.block_number

    send("Listening for new pairs...")

    while True:

        block=w3.eth.block_number

        if block>last_block:

            events=factory.events.PairCreated.get_logs(
                from_block=last_block+1,
                to_block=block
            )

            for e in events:

                token0=e["args"]["token0"]
                token1=e["args"]["token1"]
                pair=e["args"]["pair"]

                token=None

                if token0.lower()==WETH.lower():
                    token=token1

                if token1.lower()==WETH.lower():
                    token=token0

                if token:

                    process_pair(
                        Web3.to_checksum_address(token),
                        Web3.to_checksum_address(pair)
                    )

            last_block=block

        time.sleep(1)

# -------------------------
# PORTFOLIO LOOP
# -------------------------

def portfolio_loop():

    while True:

        total=ACCOUNT_CASH

        for t in PAPER_TRADES.values():

            data=get_pair_data(t.pair)

            if not data:
                continue

            total+=t.tokens*data["price"]

        pnl=total-START_BALANCE

        send(
f"""📊 PORTFOLIO

Balance ${total:.2f}

Profit ${pnl:.2f}

Cash ${ACCOUNT_CASH:.2f}

Open Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}"""
)

        time.sleep(PORTFOLIO_UPDATE_SECONDS)

# -------------------------
# MAIN
# -------------------------

def main():

    send(
f"""Launch Sniper Started

Mode {"LIVE" if RUN_PURCHASE=="on" else "PAPER"}

Balance ${START_BALANCE}"""
)

    threading.Thread(target=event_listener,daemon=True).start()
    threading.Thread(target=portfolio_loop,daemon=True).start()

    while True:
        time.sleep(60)

# -------------------------

if __name__=="__main__":
    main()
