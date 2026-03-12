import time
import os
import requests
from web3 import Web3

# ---------------------------
# CONFIG
# ---------------------------

NODE = os.getenv("NODE")  # Your WSS URL
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")

# ---------------------------
# CONNECT WEB3
# ---------------------------

w3 = Web3(Web3.LegacyWebSocketProvider(NODE))
if w3.is_connected():
    print("✅ Connected to Ethereum WebSocket node")
else:
    print("❌ Failed to connect — check NODE variable")

# ---------------------------
# TELEGRAM ALERT
# ---------------------------

def send(msg):
    if TELEGRAM_TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
        except Exception as e:
            print(f"Telegram send error: {e}")
    print(msg)

# ---------------------------
# HONEYPOT CHECK
# ---------------------------

def honeypot_check(token):
    """Return True if token is NOT a honeypot."""
    url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
    try:
        r = requests.get(url).json()
        result = r.get("result", {}).get(token.lower(), {})
        return result.get("is_honeypot") == "0"
    except Exception as e:
        print(f"Honeypot check error: {e}")
        return False

# ---------------------------
# GET TOKEN METADATA
# ---------------------------

ERC20_ABI = [
    {"constant":True,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
]

def get_token_info(token):
    try:
        token_contract = w3.eth.contract(address=token, abi=ERC20_ABI)
        name = token_contract.functions.name().call()
        symbol = token_contract.functions.symbol().call()
        return name, symbol
    except Exception:
        return "Unknown", "Unknown"

# ---------------------------
# FACTORY CONTRACT
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

factory_contract = w3.eth.contract(address=FACTORY, abi=factory_abi)

# ---------------------------
# MAIN LOOP
# ---------------------------

def main_loop():
    event_filter = factory_contract.events.PairCreated.create_filter(from_block="latest")
    send("🚀 Alert Bot Started: Monitoring new WETH pairs...")
    
    while True:
        try:
            for event in event_filter.get_new_entries():
                t0 = event["args"]["token0"]
                t1 = event["args"]["token1"]
                token = t1 if t0.lower() == WETH.lower() else t0

                name, symbol = get_token_info(token)

                if honeypot_check(token):
                    send(f"✅ New Pair Passed Security Check: {name} ({symbol})\nAddress: {token}")
                else:
                    send(f"⚠️ New Pair Failed Security Check (Potential Honeypot): {name} ({symbol})\nAddress: {token}")
            
            time.sleep(2)

        except Exception as e:
            print(f"Connection error, restarting filter: {e}")
            time.sleep(5)
            event_filter = factory_contract.events.PairCreated.create_filter(from_block="latest")

if __name__ == "__main__":
    main_loop()
