import time
import os
import requests
from web3 import Web3
import spacy
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import json
import subprocess
import threading
import asyncio
import aiohttp
from web3.middleware import geth_poa  # Corrected import for Web3.py v6+

# ---------------------------
# CONFIG
# ---------------------------
NODE = os.getenv("NODE")  # Your WSS URL
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")  # Optional metadata

WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
MIN_LIQUIDITY_ETH = 10
MIN_FIRST_SWAP_VOLUME = 0.05
POLL_INTERVAL = 1  # Fast polling

# ---------------------------
# WEB3 CONNECTION
# ---------------------------
w3 = Web3(Web3.LegacyWebSocketProvider(NODE))
w3.middleware_onion.inject(geth_poa, layer=0)  # For PoA chains or BSC

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
            requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})
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
# ERC20 ABI
# ---------------------------
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
]

# ---------------------------
# GET TOKEN INFO
# ---------------------------
def get_token_info(token):
    try:
        token_contract = w3.eth.contract(address=token, abi=ERC20_ABI)
        name = token_contract.functions.name().call()
        symbol = token_contract.functions.symbol().call()
        return name, symbol
    except:
        return "Unknown", "Unknown"

# ---------------------------
# FETCH VERIFIED / METADATA
# ---------------------------
def get_token_links(token, symbol):
    website = f"https://etherscan.io/token/{token}"
    social = f"https://t.me/{symbol}"
    verified = False
    try:
        url = f"https://api.etherscan.io/api?module=token&action=getTokenInfo&contractaddress={token}&apikey={ETHERSCAN_API_KEY}"
        r = requests.get(url).json()
        result = r.get("result", {})
        website = result.get("website", website)
        social = result.get("telegram", social)
        verified = result.get("is_verified", "0") == "1"
    except:
        pass
    return website, social, verified

# ---------------------------
# SPACY NLP
# ---------------------------
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    print("Downloading spaCy en_core_web_sm model...")
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
    nlp = spacy.load("en_core_web_sm")

with open("big_names.json") as f:
    BIG_NAMES = json.load(f)

KEYWORDS = ["token", "coin", "crypto", "project", "launch", "swap"]

def scan_entities(text, token_address):
    alerts = []
    doc = nlp(text)
    for ent in doc.ents:
        if ent.text in BIG_NAMES:
            start = max(ent.start_char - 50, 0)
            end = min(ent.end_char + 50, len(text))
            context = text[start:end].lower()
            if any(k in context for k in KEYWORDS):
                alerts.append({
                    "address": token_address,
                    "name": ent.text,
                    "indicator": "🔵"
                })
    return alerts

# ---------------------------
# ASYNC WEBSITE / SOCIAL SCRAPING
# ---------------------------
async def fetch_text_from_url(session, url):
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                return soup.get_text(separator=" ", strip=True)
    except:
        pass
    return ""

# ---------------------------
# PAIR ABI & LIQUIDITY
# ---------------------------
PAIR_ABI = [
    {"constant": True,"inputs":[],"name":"getReserves","outputs":[
        {"name":"_reserve0","type":"uint112"},
        {"name":"_reserve1","type":"uint112"},
        {"name":"_blockTimestampLast","type":"uint32"}],"type":"function"},
    {"constant": True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"constant": True,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},
]

def check_liquidity(pair_address):
    try:
        pair_contract = w3.eth.contract(address=pair_address, abi=PAIR_ABI)
        reserves = pair_contract.functions.getReserves().call()
        token0 = pair_contract.functions.token0().call()
        token1 = pair_contract.functions.token1().call()
        weth_reserve = reserves[0] if token0.lower() == WETH.lower() else reserves[1] if token1.lower() == WETH.lower() else 0
        return w3.from_wei(weth_reserve, "ether")
    except:
        return 0

def is_tradable(token, pair):
    try:
        pair_contract = w3.eth.contract(address=pair, abi=PAIR_ABI)
        token_contract = w3.eth.contract(address=token, abi=ERC20_ABI)
        reserves = pair_contract.functions.getReserves().call()
        token0 = pair_contract.functions.token0().call()
        token1 = pair_contract.functions.token1().call()
        weth_reserve = reserves[0] if token0.lower() == WETH.lower() else reserves[1] if token1.lower() == WETH.lower() else 0
        if w3.from_wei(weth_reserve, "ether") < MIN_FIRST_SWAP_VOLUME:
            return False
        balance = token_contract.functions.balanceOf(pair).call()
        if balance <= 0:
            return False
        ROUTER_ADDRESS = Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
        ROUTER_ABI = [{"constant":True,"inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"name":"amounts","type":"uint256[]"}],"type":"function"}]
        router = w3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)
        path = [token, WETH] if token != WETH else [WETH, token]
        small_amount = 10 ** 12
        router.functions.getAmountsOut(small_amount, path).call()
        return True
    except:
        return False

# ---------------------------
# FACTORY CONTRACT
# ---------------------------
factory_abi = [
    {"anonymous": False,"inputs":[
        {"indexed": True, "name": "token0", "type": "address"},
        {"indexed": True, "name": "token1", "type": "address"},
        {"indexed": False, "name": "pair", "type": "address"},
        {"indexed": False, "name": "", "type": "uint256"}],
     "name": "PairCreated","type":"event"}
]
factory_contract = w3.eth.contract(address=FACTORY, abi=factory_abi)

# ---------------------------
# PROCESS NEW TOKEN IN THREAD
# ---------------------------
def process_new_token(token, pair_address):
    def worker():
        if not honeypot_check(token):
            return
        liquidity = check_liquidity(pair_address)
        while liquidity < MIN_LIQUIDITY_ETH or not is_tradable(token, pair_address):
            time.sleep(POLL_INTERVAL)
            liquidity = check_liquidity(pair_address)

        name, symbol = get_token_info(token)
        website, social, verified = get_token_links(token, symbol)
        status_tag = "✅ *Verified*" if verified else "⚠️ *Unverified*"
        dextools = f"https://www.dextools.io/app/en/ether/pair-explorer/{pair_address}"

        # Async fetch website/social text
        async def fetch_all_text():
            async with aiohttp.ClientSession() as session:
                texts = await asyncio.gather(*[fetch_text_from_url(session, url) for url in [website, social]])
                return " ".join(texts)

        extra_text = asyncio.run(fetch_all_text())
        text_to_scan = f"{name} {symbol} {website} {social} {extra_text}"
        entity_alerts = scan_entities(text_to_scan, token)
        for alert in entity_alerts:
            alert_msg = f"{alert['indicator']} *Entity Detected*\nToken: {alert['address']}\nMention: {alert['name']}"
            send(alert_msg)

        msg = (
            f"🚨 *HIGH-POTENTIAL TOKEN DETECTED / FIRST BUY*\n\n"
            f"{status_tag}\n"
            f"*{name} ({symbol})*\n\n"
            f"💰 *Liquidity:* {liquidity:.2f} ETH\n\n"
            f"🌐 *Website*\n{website}\n\n"
            f"📱 *Social*\n{social}\n\n"
            f"📊 *DexTools*\n{dextools}\n\n"
            f"📋 *Token Address (Tap to Copy)*\n```{token}```\n\n"
            f"📋 *Pair Address*\n```{pair_address}```"
        )
        send(msg)
    threading.Thread(target=worker).start()

# ---------------------------
# MAIN LOOP
# ---------------------------
def main_loop():
    send("🚀 Real-Time High-Potential Alert Bot Started")
    event_filter = factory_contract.events.PairCreated.create_filter(fromBlock='latest')

    while True:
        try:
            for event in event_filter.get_new_entries():
                t0 = event['args']['token0']
                t1 = event['args']['token1']
                pair_address = event['args']['pair']
                token = t1 if t0.lower() == WETH.lower() else t0
                threading.Thread(target=process_new_token, args=(token, pair_address)).start()

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"Error fetching events: {e}")
            time.sleep(5)
            event_filter = factory_contract.events.PairCreated.create_filter(fromBlock='latest')

if __name__ == "__main__":
    main_loop()
