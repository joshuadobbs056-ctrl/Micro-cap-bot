import subprocess
import sys


def install(package: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


try:
    import requests
except ImportError:
    install("requests")
    import requests

try:
    from web3 import Web3
except ImportError:
    install("web3")
    from web3 import Web3

import os
import time
import threading
from collections import defaultdict

# ---------------------------
# CONFIG
# ---------------------------
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")

# Binary-style early runner filters
MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY", "1.5"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY", "25"))

LIQUIDITY_WAIT_SECONDS = int(os.getenv("LIQUIDITY_WAIT_SECONDS", "120"))
TRACK_SECONDS = int(os.getenv("TRACK_SECONDS", "90"))

MONEY_MIN_BUYS = int(os.getenv("MONEY_MIN_BUYS", "5"))
MONEY_MIN_UNIQUE_BUYERS = int(os.getenv("MONEY_MIN_UNIQUE_BUYERS", "5"))
MONEY_MIN_BUY_ETH = float(os.getenv("MONEY_MIN_BUY_ETH", "1.0"))
MONEY_MIN_BUYER_VELOCITY = float(os.getenv("MONEY_MIN_BUYER_VELOCITY", "3.0"))  # unique buyers per minute
REQUIRE_ONE_SUCCESSFUL_SELL = os.getenv("REQUIRE_ONE_SUCCESSFUL_SELL", "true").lower() == "true"

# Upgrade filter to avoid obvious single-wallet bursts
MAX_TOP_BUYER_SHARE = float(os.getenv("MAX_TOP_BUYER_SHARE", "0.45"))

BLOCK_POLL_SECONDS = float(os.getenv("BLOCK_POLL_SECONDS", "2"))
PAIR_POLL_SECONDS = float(os.getenv("PAIR_POLL_SECONDS", "1.5"))

# ---------------------------
# WEB3
# ---------------------------
if not NODE:
    raise RuntimeError("NODE env var is required.")

w3 = Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Failed to connect to Ethereum node.")

print("Connected to Ethereum node")

# ---------------------------
# TELEGRAM
# ---------------------------
def send(msg: str) -> None:
    if TELEGRAM_TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "text": msg,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
        except Exception as e:
            print(f"Telegram error: {e}")
    print(msg)


def send_copy_bubble(label: str, value: str) -> None:
    send(f"{label}\n{value}")


# ---------------------------
# HELPERS
# ---------------------------
def now_ts() -> float:
    return time.time()


def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def short_addr(addr: str) -> str:
    if not addr:
        return "unknown"
    addr = str(addr)
    if len(addr) < 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def dextools_link(pair: str) -> str:
    return f"https://www.dextools.io/app/en/ether/pair-explorer/{pair}"


def dexscreener_link(pair: str) -> str:
    return f"https://dexscreener.com/ethereum/{pair}"


# ---------------------------
# ABIS
# ---------------------------
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

PAIR_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": False, "name": "amount0In", "type": "uint256"},
            {"indexed": False, "name": "amount1In", "type": "uint256"},
            {"indexed": False, "name": "amount0Out", "type": "uint256"},
            {"indexed": False, "name": "amount1Out", "type": "uint256"},
            {"indexed": True, "name": "to", "type": "address"},
        ],
        "name": "Swap",
        "type": "event",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "type": "function",
    },
    {"constant": True, "inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "type": "function"},
]

FACTORY_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": False, "name": "pair", "type": "address"},
            {"indexed": False, "name": "", "type": "uint256"},
        ],
        "name": "PairCreated",
        "type": "event",
    }
]

factory_contract = w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)

# ---------------------------
# TOKEN / RISK
# ---------------------------
def get_token_info(token: str) -> tuple[str, str]:
    try:
        token_contract = w3.eth.contract(address=token, abi=ERC20_ABI)
        name = safe_call(lambda: token_contract.functions.name().call(), "Unknown")
        symbol = safe_call(lambda: token_contract.functions.symbol().call(), "Unknown")
        return str(name), str(symbol)
    except Exception:
        return "Unknown", "Unknown"


def honeypot_check(token: str) -> tuple[bool, str]:
    url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
    try:
        data = requests.get(url, timeout=10).json()
        result = data.get("result", {}).get(token.lower(), {})
        is_honeypot = result.get("is_honeypot")
        if is_honeypot == "1":
            return False, "honeypot"
        return True, "ok"
    except Exception as e:
        return False, f"risk_check_error: {e}"


# ---------------------------
# LIQUIDITY
# ---------------------------
def get_pair_contract(pair: str):
    return w3.eth.contract(address=pair, abi=PAIR_ABI)


def check_liquidity_eth(pair: str) -> float:
    try:
        pair_contract = get_pair_contract(pair)
        reserves = pair_contract.functions.getReserves().call()
        token0 = pair_contract.functions.token0().call()
        token1 = pair_contract.functions.token1().call()

        weth_reserve = 0
        if token0.lower() == WETH.lower():
            weth_reserve = reserves[0]
        elif token1.lower() == WETH.lower():
            weth_reserve = reserves[1]

        return float(w3.from_wei(weth_reserve, "ether"))
    except Exception:
        return 0.0


# ---------------------------
# BUY / SELL PARSING
# ---------------------------
def parse_swap_direction(args: dict, token0: str, token1: str):
    amount0_in = int(args.get("amount0In", 0))
    amount1_in = int(args.get("amount1In", 0))
    amount0_out = int(args.get("amount0Out", 0))
    amount1_out = int(args.get("amount1Out", 0))

    if token0.lower() == WETH.lower():
        # buy token with WETH
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount0_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, buyer
        # sell token for WETH
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount0_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, seller

    if token1.lower() == WETH.lower():
        # buy token with WETH
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount1_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, buyer
        # sell token for WETH
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount1_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, seller

    return None, 0.0, None


# ---------------------------
# CORE TRACKER
# ---------------------------
ACTIVE_PAIRS = set()


def process_new_token(token: str, pair: str) -> None:
    if pair in ACTIVE_PAIRS:
        return

    ACTIVE_PAIRS.add(pair)

    def worker():
        try:
            pair_contract = get_pair_contract(pair)
            token0 = pair_contract.functions.token0().call()
            token1 = pair_contract.functions.token1().call()

            # Wait for liquidity to appear
            liquidity = 0.0
            started = now_ts()

            while now_ts() - started < LIQUIDITY_WAIT_SECONDS:
                liquidity = check_liquidity_eth(pair)
                if liquidity >= MIN_ETH_LIQUIDITY:
                    break
                time.sleep(PAIR_POLL_SECONDS)

            if liquidity < MIN_ETH_LIQUIDITY:
                return

            if liquidity > MAX_ETH_LIQUIDITY:
                return

            ok, risk_reason = honeypot_check(token)
            if not ok:
                print(f"Risk check failed for {token}: {risk_reason}")
                return

            name, symbol = get_token_info(token)

            buy_count = 0
            sell_count = 0
            buy_eth = 0.0
            sell_eth = 0.0
            unique_buyers = set()
            unique_sellers = set()
            buyer_counts = defaultdict(int)

            track_start_ts = now_ts()
            start_block = w3.eth.block_number

            while now_ts() - track_start_ts < TRACK_SECONDS:
                current_block = w3.eth.block_number
                if current_block >= start_block:
                    try:
                        events = pair_contract.events.Swap.get_logs(
                            from_block=start_block,
                            to_block=current_block,
                        )
                    except Exception as e:
                        print(f"Swap log read error for {pair}: {e}")
                        time.sleep(PAIR_POLL_SECONDS)
                        continue

                    start_block = current_block + 1

                    for event in events:
                        side, eth_amount, wallet = parse_swap_direction(
                            event["args"], token0, token1
                        )

                        if side == "buy":
                            buy_count += 1
                            buy_eth += eth_amount
                            if wallet:
                                wallet = str(wallet)
                                unique_buyers.add(wallet)
                                buyer_counts[wallet] += 1

                        elif side == "sell":
                            sell_count += 1
                            sell_eth += eth_amount
                            if wallet:
                                unique_sellers.add(str(wallet))

                time.sleep(PAIR_POLL_SECONDS)

            elapsed_minutes = max((now_ts() - track_start_ts) / 60.0, 0.01)
            unique_buyer_count = len(unique_buyers)
            buyer_velocity = unique_buyer_count / elapsed_minutes

            top_buyer_share = 0.0
            if buy_count > 0 and buyer_counts:
                top_buyer_share = max(buyer_counts.values()) / buy_count

            if buy_count < MONEY_MIN_BUYS:
                return
            if unique_buyer_count < MONEY_MIN_UNIQUE_BUYERS:
                return
            if buy_eth < MONEY_MIN_BUY_ETH:
                return
            if buyer_velocity < MONEY_MIN_BUYER_VELOCITY:
                return
            if REQUIRE_ONE_SUCCESSFUL_SELL and sell_count < 1:
                return
            if top_buyer_share > MAX_TOP_BUYER_SHARE:
                return

            msg = (
                "💰 MONEY SIGNAL\n\n"
                f"{name} ({symbol})\n\n"
                f"Liquidity: {liquidity:.2f} ETH\n"
                f"Buys: {buy_count}\n"
                f"Sells: {sell_count}\n"
                f"Unique buyers: {unique_buyer_count}\n"
                f"Unique sellers: {len(unique_sellers)}\n"
                f"Buy ETH: {buy_eth:.2f}\n"
                f"Sell ETH: {sell_eth:.2f}\n"
                f"Buyer velocity: {buyer_velocity:.2f}/min\n"
                f"Top buyer share: {top_buyer_share:.0%}\n"
                f"Sellability: {'PASS' if sell_count >= 1 else 'FAIL'}\n"
                f"Risk check: {risk_reason}\n\n"
                "DexTools\n"
                f"{dextools_link(pair)}\n\n"
                "DexScreener\n"
                f"{dexscreener_link(pair)}"
            )
            send(msg)
            send_copy_bubble("TOKEN", token)
            send_copy_bubble("PAIR", pair)

        except Exception as e:
            print(f"Worker error for pair {pair}: {e}")
        finally:
            ACTIVE_PAIRS.discard(pair)

    threading.Thread(target=worker, daemon=True).start()


# ---------------------------
# EVENT HANDLER
# ---------------------------
def handle_event(event) -> None:
    t0 = event["args"]["token0"]
    t1 = event["args"]["token1"]
    pair = event["args"]["pair"]

    token = None
    if str(t0).lower() == WETH.lower():
        token = t1
    elif str(t1).lower() == WETH.lower():
        token = t0

    if not token:
        return

    process_new_token(Web3.to_checksum_address(token), Web3.to_checksum_address(pair))


# ---------------------------
# MAIN LOOP
# ---------------------------
def main_loop() -> None:
    send("ETH early-run scanner started")

    last_block = w3.eth.block_number

    while True:
        try:
            current_block = w3.eth.block_number

            if current_block > last_block:
                events = factory_contract.events.PairCreated.get_logs(
                    from_block=last_block + 1,
                    to_block=current_block,
                )

                for event in events:
                    handle_event(event)

                last_block = current_block

            time.sleep(BLOCK_POLL_SECONDS)

        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(5)


# ---------------------------
# START
# ---------------------------
if __name__ == "__main__":
    main_loop()
