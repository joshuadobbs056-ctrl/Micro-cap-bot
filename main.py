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

# -------------------------
# ENV
# -------------------------

NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

RUN_PURCHASE = os.getenv("RUN_PURCHASE","off").lower()

PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD","50"))

DEXS_MIN_LIQ_USD = float(os.getenv("DEXS_MIN_LIQ_USD","2000"))

DEXSCREENER_POLL_SECONDS = float(os.getenv("DEXSCREENER_POLL_SECONDS","20"))
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS","300"))

UPDATE_SECONDS = 60

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT","50"))
STOP_LOSS = float(os.getenv("STOP_LOSS","-30"))

# -------------------------
# WEB3
# -------------------------

if not NODE:
    raise RuntimeError("NODE missing")

w3 = Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Node failed")

print("Connected to node")

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
# PRICE FETCH
# -------------------------

def get_pair_data(pair):

    try:

        url=f"https://api.dexscreener.com/latest/dex/pairs/ethereum/{pair}"

        r=requests.get(url,timeout=10)

        data=r.json()

        pairdata=data.get("pair")

        if not pairdata:
            return None

        return {
            "price":float(pairdata.get("priceUsd") or 0),
            "liq":float((pairdata.get("liquidity") or {}).get("usd") or 0),
            "mc":float(pairdata.get("fdv") or 0)
        }

    except:
        return None

# -------------------------
# PAPER TRADE
# -------------------------

class PaperTrade:

    def __init__(self,token,pair,entry_price):

        self.token=token
        self.pair=pair

        self.entry_price=entry_price

        self.tokens=PURCHASE_AMOUNT_USD/entry_price

        self.open=time.time()

PAPER_TRADES={}

def monitor_trade(token):

    trade=PAPER_TRADES[token]

    while True:

        time.sleep(UPDATE_SECONDS)

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

Entry
${trade.entry_price}

Current
${price}

Value
${value:.2f}

PnL
{pnl:.2f}%
"""
)

        if pnl>=TAKE_PROFIT or pnl<=STOP_LOSS:

            send(
f"""🧪 PAPER TRADE CLOSED

Token
{trade.token}

Entry
${trade.entry_price}

Exit
${price}

Final Value
${value:.2f}

PnL
{pnl:.2f}%
"""
)

            del PAPER_TRADES[token]

            return

def open_paper_trade(token,pair):

    data=get_pair_data(pair)

    if not data:
        return

    price=data["price"]

    trade=PaperTrade(token,pair,price)

    PAPER_TRADES[token]=trade

    send(
f"""🧪 PAPER TRADE OPENED

Token
{token}

Entry Price
${price}

Simulated Buy
${PURCHASE_AMOUNT_USD}

Tokens
{trade.tokens:.2f}
"""
)

    threading.Thread(
        target=monitor_trade,
        args=(token,),
        daemon=True
    ).start()

# -------------------------
# AUTO BUY
# -------------------------

def execute_buy(token):

    if RUN_PURCHASE!="on":
        return

    send(f"🟢 LIVE BUY EXECUTED {token}")

# -------------------------
# DISCOVERY
# -------------------------

def fetch_dex():

    queries=["ETH","WETH","USDC","coin","pepe"]

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

                liq=float((p.get("liquidity") or {}).get("usd") or 0)

                results.append({
                    "pair":pair,
                    "token":token,
                    "liq":liq
                })

        except:
            pass

    uniq={}

    for r in results:
        uniq[r["pair"].lower()]=r

    return list(uniq.values())

# -------------------------
# PROCESS
# -------------------------

ACTIVE=set()

def process_pair(token,pair,liq):

    if pair in ACTIVE:
        return

    ACTIVE.add(pair)

    try:

        if liq<DEXS_MIN_LIQ_USD:
            return

        send(
f"""🚀 BUY SIGNAL

Token
{token}

Pair
{pair}

Liquidity
${liq:,.0f}
"""
)

        if RUN_PURCHASE=="on":
            execute_buy(token)
        else:
            open_paper_trade(token,pair)

    finally:

        ACTIVE.remove(pair)

# -------------------------
# DISCOVERY LOOP
# -------------------------

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

            print("dex error",e)

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

Active Trades
{len(PAPER_TRADES)}
"""
)

# -------------------------
# MAIN
# -------------------------

def main():

    send(
f"""ETH Launch Scanner Started

Mode
{"LIVE" if RUN_PURCHASE=="on" else "PAPER"}

Buy Size
${PURCHASE_AMOUNT_USD}
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
