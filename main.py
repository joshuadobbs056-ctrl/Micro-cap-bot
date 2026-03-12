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
MIN_LIQUIDITY_ETH = 10  # minimum WETH liquidity

# ---------------------------
# CONNECT WEB3
# ---------------------------
w3 = Web3(Web3.LegacyWebSocketProvider(NODE))
if not w3.is_connected():
    print("❌ Failed to connect — check NODE variable")
else:
    print("✅ Connected to Ethereum WebSocket node")

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
    url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
    try:
        r = requests.get(url).json()
        result = r.get("result", {}).get(token.lower(), {})
        return result.get("is_honeypot") == "0"
    except:
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
    except:
        return "Unknown", "Unknown"

# ---------------------------
# GET PAIR LIQUIDITY
# ---------------------------
PAIR_ABI = [
    {"constant":True,"inputs":[],"name":"getReserves","outputs":[
        {"name":"_reserve0","type":"uint112"},
        {"name":"_reserve1","type":"uint112"},
        {"name":"_blockTimestampLast","type":"uint32"}
    ],"type":"function"},
    {"constant":True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"}
]

def check_liquidity(pair_address):
    try:
        pair_contract = w3.eth.contract(address=pair_address, abi=PAIR_ABI)
        reserves = pair_contract.functions.getReserves().call()
        token0 = pair_contract.functions.token0().call()
        token1 = pair_contract.functions.token1().call()
        # WETH is token0 or token1
        if token0.lower() == WETH.lower():
            weth_reserve = reserves[0]
        elif token1.lower() == WETH.lower():
            weth_reserve = reserves[1]
        else:
            return 0
        # Convert to ETH
        return w3.from_wei(weth_reserve, 'ether')
    except:
        return 0

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
    send("🚀 High-Potential Alert Bot Started: Only safe + high liquidity tokens...")
    
    while True:
        try:
            for event in event_filter.get_new_entries():
                t0 = event["args"]["token0"]
                t1 = event["args"]["token1"]
                pair_address = event["args"]["pair"]
                token = t1 if t0.lower() == WETH.lower() else t0

                if not honeypot_check(token):
                    continue  # skip unsafe token

                liquidity = check_liquidity(pair_address)
                if liquidity < MIN_LIQUIDITY_ETH:
                    continue  # skip low liquidity

                name, symbol = get_token_info(token)
                send(f"✅ High-Potential Token Detected: {name} ({symbol})\nAddress: {token}\nWETH Liquidity: {liquidity:.2f} ETH")

            time.sleep(2)
        except Exception as e:
            print(f"Connection error, restarting filter: {e}")
            time.sleep(5)
            event_filter = factory_contract.events.PairCreated.create_filter(from_block="latest")

if __name__ == "__main__":
    main_loop()
