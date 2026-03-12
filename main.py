import time
import requests
from web3 import Web3

# ---------------------------
# CONFIG (Ensure these are set in Railway Variables!)
# ---------------------------

NODE = "YOUR_ETH_NODE_WEBSOCKET_URL" # Must start with ws:// or wss://
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

# FIX: Updated to WebSocketProvider (v6+ syntax)
try:
    w3 = Web3(Web3.WebSocketProvider(NODE))
    if not w3.is_connected():
        print("Failed to connect to the node. Check your NODE URL.")
except AttributeError:
    # Fallback for some specific environment configurations
    w3 = Web3(Web3.LegacyWebSocketProvider(NODE))

account = w3.eth.account.from_key(PRIVATE_KEY)

# ---------------------------
# TELEGRAM ALERT
# ---------------------------

def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": msg
    }
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Telegram error: {e}")

# ---------------------------
# HONEYPOT CHECK
# ---------------------------

def honeypot_check(token):
    url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
    try:
        r = requests.get(url).json()
        # GoPlus returns "1" as a string if it's a honeypot
        honeypot = r["result"][token.lower()].get("is_honeypot", "0")
        if honeypot == "1":
            return False
    except Exception as e:
        print(f"Honeypot check error: {e}")
        return False # Safety first: if check fails, don't buy

    return True

# ---------------------------
# BUY TOKEN
# ---------------------------

def buy_token(token):
    amount = w3.to_wei(BUY_AMOUNT_ETH, 'ether')

    router = w3.eth.contract(
        address=UNISWAP_ROUTER,
        abi=[{
            "name": "swapExactETHForTokens",
            "type": "function",
            "inputs": [
                {"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"}
            ],
            "outputs": [{"name": "amounts", "type": "uint256[]"}],
            "stateMutability": "payable"
        }]
    )

    try:
        tx = router.functions.swapExactETHForTokens(
            0, # Note: Setting 0 slippage is risky but common for snipers
            [
                w3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2"), # WETH
                w3.to_checksum_address(token)
            ],
            WALLET_ADDRESS,
            int(time.time()) + 600
        ).build_transaction({
            "from": WALLET_ADDRESS,
            "value": amount,
            "gas": 300000,
            "gasPrice": w3.eth.gas_price, # Dynamic gas price is safer
            "nonce": w3.eth.get_transaction_count(WALLET_ADDRESS)
        })

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        send(f"🚀 Bought token\n{token}\nTx: {tx_hash.hex()}")
    except Exception as e:
        send(f"❌ Buy failed: {e}")

# ---------------------------
# LISTEN FOR NEW PAIRS
# ---------------------------

factory_abi = [{
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "token0", "type": "address"},
        {"indexed": True, "name": "token1", "type": "address"},
        {"indexed": False, "name": "pair", "type": "address"},
        {"indexed": False, "name": "", "type": "uint256"}
    ],
    "name": "PairCreated",
    "type": "event"
}]

factory = w3.eth.contract(address=FACTORY, abi=factory_abi)

# ---------------------------
# MAIN LOOP
# ---------------------------

send("🚀 Micro-cap-bot Started on Railway")

while True:
    try:
        # Re-creating filter or using get_logs is often more stable on hosted nodes
        event_filter = factory.events.PairCreated.create_filter(fromBlock='latest')
        
        while True:
            for event in event_filter.get_new_entries():
                token0 = event["args"]["token0"]
                token1 = event["args"]["token1"]

                # Usually, we want the one that ISN'T WETH
                weth = "0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2"
                token = token1 if token0.lower() == weth.lower() else token0

                send(f"🆕 New Pair Detected\nToken: {token}")

                if honeypot_check(token):
                    send("✅ Passed Honeypot Check")
                    buy_token(token)
                else:
                    send("❌ Honeypot Detected - Skipping")

            time.sleep(2)
    except Exception as e:
        print(f"Loop error: {e}")
        time.sleep(10) # Wait before restarting loop if node disconnects
