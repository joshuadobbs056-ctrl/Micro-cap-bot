import time
import os
import requests
from web3 import Web3
import eth_utils

# ---------------------------
# CONFIG (Set these in Railway -> Variables)
# ---------------------------

# Use os.getenv so your keys aren't exposed in your code
NODE = os.getenv("NODE", "YOUR_ETH_NODE_WSS_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "0x000")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "0x000")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BUY_AMOUNT_ETH = 0.02

UNISWAP_ROUTER = Web3.to_checksum_address(
    "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
)

FACTORY = Web3.to_checksum_address(
    "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
)

# ---------------------------
# CONNECT WEB3
# ---------------------------

# Fix for the WebSocketProvider v6+ error
w3 = Web3(Web3.WebSocketProvider(NODE))

# Robust private key handling to prevent "Odd-length string" errors
try:
    if PRIVATE_KEY.startswith("0x"):
        clean_key = PRIVATE_KEY
    else:
        clean_key = "0x" + PRIVATE_KEY
    
    account = w3.eth.account.from_key(clean_key)
    print(f"Connected as: {account.address}")
except Exception as e:
    print(f"CRITICAL: Private Key Error - {e}")

# ---------------------------
# TELEGRAM ALERT
# ---------------------------

def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"Telegram Log: {msg}")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data)
    except:
        pass

# ---------------------------
# HONEYPOT CHECK
# ---------------------------

def honeypot_check(token):
    url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
    try:
        r = requests.get(url).json()
        result = r.get("result", {}).get(token.lower(), {})
        if result.get("is_honeypot") == "1":
            return False
        return True
    except:
        return False

# ---------------------------
# BUY TOKEN
# ---------------------------

def buy_token(token):
    amount = w3.to_wei(BUY_AMOUNT_ETH, 'ether')
    
    # Minimal ABI for the swap function
    router = w3.eth.contract(
        address=UNISWAP_ROUTER,
        abi=[{"name":"swapExactETHForTokens","type":"function","inputs":[{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],"outputs":[{"name":"amounts","type":"uint256[]"}],"stateMutability":"payable"}]
    )

    try:
        tx = router.functions.swapExactETHForTokens(
            0, # 0 = No slippage protection (Risky!)
            [w3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2"), w3.to_checksum_address(token)],
            WALLET_ADDRESS,
            int(time.time()) + 600
        ).build_transaction({
            "from": WALLET_ADDRESS,
            "value": amount,
            "gas": 300000,
            "gasPrice": w3.eth.gas_price,
            "nonce": w3.eth.get_transaction_count(WALLET_ADDRESS)
        })

        signed = w3.eth.account.sign_transaction(tx, clean_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        send(f"🚀 Bought token\n{token}\nTx: {tx_hash.hex()}")
    except Exception as e:
        send(f"❌ Buy Error: {str(e)[:100]}")

# ---------------------------
# MAIN LOOP
# ---------------------------

factory_abi = [{"anonymous":False,"inputs":[{"indexed":True,"name":"token0","type":"address"},{"indexed":True,"name":"token1","type":"address"},{"indexed":False,"name":"pair","type":"address"},{"indexed":False,"name":"","type":"uint256"}],"name":"PairCreated","type":"event"}]
factory_contract = w3.eth.contract(address=FACTORY, abi=factory_abi)

send("🚀 Bot Started & Monitoring...")

while True:
    try:
        # Create filter and listen
        event_filter = factory_contract.events.PairCreated.create_filter(fromBlock='latest')
        while True:
            for event in event_filter.get_new_entries():
                t0 = event["args"]["token0"]
                t1 = event["args"]["token1"]
                
                # Determine which one is the new token (not WETH)
                weth = "0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2"
                token = t1 if t0.lower() == weth.lower() else t0
                
                send(f"🆕 New Pair: {token}")
                
                if honeypot_check(token):
                    send("✅ Safe - Buying...")
                    buy_token(token)
                else:
                    send("⚠️ Honeypot - Skipping")
            
            time.sleep(2)
    except Exception as e:
        print(f"Restarting loop due to error: {e}")
        time.sleep(5)
