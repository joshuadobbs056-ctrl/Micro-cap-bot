import time
import requests
from web3 import Web3

# ---------------------------
# CONFIG
# ---------------------------

NODE = "YOUR_ETH_NODE"
PRIVATE_KEY = "YOUR_PRIVATE_KEY"
WALLET_ADDRESS = "YOUR_WALLET"

TELEGRAM_TOKEN = "BOT_TOKEN"
CHAT_ID = "CHAT_ID"

BUY_AMOUNT_ETH = 0.02
MIN_LIQUIDITY_USD = 50000

UNISWAP_ROUTER = Web3.to_checksum_address(
"0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
)

FACTORY = Web3.to_checksum_address(
"0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
)

# ---------------------------
# CONNECT WEB3
# ---------------------------

w3 = Web3(Web3.WebsocketProvider(NODE))

account = w3.eth.account.from_key(PRIVATE_KEY)

# ---------------------------
# TELEGRAM ALERT
# ---------------------------

def send(msg):

    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    data={
        "chat_id":CHAT_ID,
        "text":msg
    }

    requests.post(url,data=data)

# ---------------------------
# HONEYPOT CHECK
# ---------------------------

def honeypot_check(token):

    url=f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"

    r=requests.get(url).json()

    try:

        honeypot=r["result"][token]["is_honeypot"]

        if honeypot=="1":
            return False

    except:
        pass

    return True

# ---------------------------
# BUY TOKEN
# ---------------------------

def buy_token(token):

    amount = w3.to_wei(BUY_AMOUNT_ETH,'ether')

    router = w3.eth.contract(
        address=UNISWAP_ROUTER,
        abi=[{
            "name":"swapExactETHForTokens",
            "type":"function",
            "inputs":[
                {"name":"amountOutMin","type":"uint256"},
                {"name":"path","type":"address[]"},
                {"name":"to","type":"address"},
                {"name":"deadline","type":"uint256"}
            ]
        }]
    )

    tx = router.functions.swapExactETHForTokens(
        0,
        [
            w3.to_checksum_address(
            "0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2"
            ),
            token
        ],
        WALLET_ADDRESS,
        int(time.time())+600
    ).build_transaction({

        "from": WALLET_ADDRESS,
        "value": amount,
        "gas":300000,
        "gasPrice":w3.to_wei("30","gwei"),
        "nonce":w3.eth.get_transaction_count(WALLET_ADDRESS)

    })

    signed = w3.eth.account.sign_transaction(tx,PRIVATE_KEY)

    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)

    send(f"🚀 Bought token\n{token}\nTx:{tx_hash.hex()}")

# ---------------------------
# LISTEN FOR NEW PAIRS
# ---------------------------

factory_abi=[{
 "anonymous":False,
 "inputs":[
  {"indexed":True,"name":"token0","type":"address"},
  {"indexed":True,"name":"token1","type":"address"},
  {"indexed":False,"name":"pair","type":"address"}
 ],
 "name":"PairCreated",
 "type":"event"
}]

factory = w3.eth.contract(address=FACTORY,abi=factory_abi)

pair_filter = factory.events.PairCreated.create_filter(fromBlock="latest")

# ---------------------------
# MAIN LOOP
# ---------------------------

send("🚀 Meme Sniper Bot Started")

while True:

    events = pair_filter.get_new_entries()

    for e in events:

        token0 = e["args"]["token0"]
        token1 = e["args"]["token1"]

        token = token0

        send(f"🆕 New Pair Detected\nToken:{token}")

        if honeypot_check(token):

            send("✅ Passed Honeypot Check")

            buy_token(token)

        else:

            send("❌ Honeypot Detected")

    time.sleep(5)
