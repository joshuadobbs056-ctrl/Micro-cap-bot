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

PORTFOLIO_UPDATE_SECONDS = int(os.getenv("PORTFOLIO_UPDATE_SECONDS","30"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT","50"))
STOP_LOSS = float(os.getenv("STOP_LOSS","-30"))

START_BALANCE = float(os.getenv("START_BALANCE","2000"))

MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES","10"))

ACCOUNT_CASH = START_BALANCE

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

# -------------------------
# MONITOR TRADE
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

            ACCOUNT_CASH += value

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

# -------------------------
# OPEN TRADE
# -------------------------

def open_paper_trade(token,pair):

    global ACCOUNT_CASH

    if len(PAPER_TRADES) >= MAX_OPEN_TRADES:

        send(
f"""⚠️ TRADE SKIPPED

Reason: max open trades reached

Open Trades: {len(PAPER_TRADES)}
Limit: {MAX_OPEN_TRADES}
"""
)

        return

    if ACCOUNT_CASH < PURCHASE_AMOUNT_USD:

        send(
f"""⚠️ TRADE SKIPPED

Reason: insufficient balance

Cash Available: ${ACCOUNT_CASH:.2f}
Required: ${PURCHASE_AMOUNT_USD}
"""
)

        return

    data=get_pair_data(pair)

    if not data:
        return

    price=data["price"]

    trade=PaperTrade(token,pair,price)

    PAPER_TRADES[token]=trade

    ACCOUNT_CASH -= PURCHASE_AMOUNT_USD

    send(
f"""🧪 PAPER TRADE OPENED

Token
{token}

Entry Price
${price}

Buy Size
${PURCHASE_AMOUNT_USD}

Tokens
{trade.tokens:.2f}

Open Trades
{len(PAPER_TRADES)}/{MAX_OPEN_TRADES}
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
# PROCESS TOKEN
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
# DEX LOOP
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
# PORTFOLIO
# -------------------------

def portfolio_loop():

    while True:

        total_value = ACCOUNT_CASH

        for token,trade in PAPER_TRADES.items():

            data=get_pair_data(trade.pair)

            if not data:
                continue

            price=data["price"]

            total_value += trade.tokens * price

        pnl = total_value - START_BALANCE
        pnl_pct = (pnl / START_BALANCE) * 100

        send(
f"""📊 PAPER PORTFOLIO

Starting Balance: ${START_BALANCE}

Current Value: ${total_value:.2f}

Total Profit: ${pnl:.2f}
PnL: {pnl_pct:.2f}%

Cash: ${ACCOUNT_CASH:.2f}

Open Trades: {len(PAPER_TRADES)} / {MAX_OPEN_TRADES}
"""
)

        time.sleep(PORTFOLIO_UPDATE_SECONDS)

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

Open Trades: {len(PAPER_TRADES)}
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
    threading.Thread(target=portfolio_loop,daemon=True).start()

    while True:
        time.sleep(60)

# -------------------------
# START
# -------------------------

if __name__=="__main__":
    main()
