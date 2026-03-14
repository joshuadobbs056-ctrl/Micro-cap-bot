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
from collections import defaultdict, deque


# ---------------------------
# CONFIG
# ---------------------------
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Wallet / auto-purchase
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
RUN_PURCHASE = os.getenv("RUN_PURCHASE", "off").strip().lower()  # on / off
PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD", "50"))
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "1500"))  # 1500 = 15%
GAS_LIMIT_BUY = int(os.getenv("GAS_LIMIT_BUY", "450000"))
GAS_LIMIT_APPROVE = int(os.getenv("GAS_LIMIT_APPROVE", "120000"))
GAS_LIMIT_SELL = int(os.getenv("GAS_LIMIT_SELL", "450000"))
BUY_DEADLINE_SECONDS = int(os.getenv("BUY_DEADLINE_SECONDS", "180"))
SELL_DEADLINE_SECONDS = int(os.getenv("SELL_DEADLINE_SECONDS", "180"))

WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
UNISWAP_V2_ROUTER = Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
CHAINLINK_ETH_USD = Web3.to_checksum_address("0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419")

# Binary-style early runner filters
MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY", "3.0"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY", "25"))

LIQUIDITY_WAIT_SECONDS = int(os.getenv("LIQUIDITY_WAIT_SECONDS", "120"))
TRACK_SECONDS = int(os.getenv("TRACK_SECONDS", "90"))

MONEY_MIN_BUYS = int(os.getenv("MONEY_MIN_BUYS", "5"))
MONEY_MIN_UNIQUE_BUYERS = int(os.getenv("MONEY_MIN_UNIQUE_BUYERS", "5"))
MONEY_MIN_BUY_ETH = float(os.getenv("MONEY_MIN_BUY_ETH", "1.0"))
MONEY_MIN_BUYER_VELOCITY = float(os.getenv("MONEY_MIN_BUYER_VELOCITY", "3.0"))  # unique buyers per minute
REQUIRE_ONE_SUCCESSFUL_SELL = os.getenv("REQUIRE_ONE_SUCCESSFUL_SELL", "true").lower() == "true"

# Filter to avoid obvious single-wallet bursts
MAX_TOP_BUYER_SHARE = float(os.getenv("MAX_TOP_BUYER_SHARE", "0.45"))

BLOCK_POLL_SECONDS = float(os.getenv("BLOCK_POLL_SECONDS", "2"))
PAIR_POLL_SECONDS = float(os.getenv("PAIR_POLL_SECONDS", "1.5"))

# ---------------------------
# TOKEN / POOL SELECTION CONFIG
# ---------------------------
ONLY_ALERT_BEST_POOL_PER_TOKEN = os.getenv("ONLY_ALERT_BEST_POOL_PER_TOKEN", "true").lower() == "true"
POOL_SELECTION_WINDOW_SECONDS = int(os.getenv("POOL_SELECTION_WINDOW_SECONDS", "45"))
POOL_SCORE_LIQUIDITY_WEIGHT = float(os.getenv("POOL_SCORE_LIQUIDITY_WEIGHT", "1.0"))
POOL_SCORE_BUY_ETH_WEIGHT = float(os.getenv("POOL_SCORE_BUY_ETH_WEIGHT", "1.4"))
POOL_SCORE_VELOCITY_WEIGHT = float(os.getenv("POOL_SCORE_VELOCITY_WEIGHT", "1.2"))
POOL_SCORE_SELL_PENALTY_WEIGHT = float(os.getenv("POOL_SCORE_SELL_PENALTY_WEIGHT", "1.2"))
POOL_SCORE_TOP_BUYER_PENALTY_WEIGHT = float(os.getenv("POOL_SCORE_TOP_BUYER_PENALTY_WEIGHT", "1.0"))
POOL_SELECTION_MIN_SCORE = float(os.getenv("POOL_SELECTION_MIN_SCORE", "0"))

# ---------------------------
# AUTO-SELL / DEFENSE CONFIG
# ---------------------------
ENABLE_POSITION_MONITOR = os.getenv("ENABLE_POSITION_MONITOR", "true").lower() == "true"
POSITION_CHECK_SECONDS = float(os.getenv("POSITION_CHECK_SECONDS", "3"))

# Liquidity-based exits
LIQUIDITY_EXIT_DROP_PCT = float(os.getenv("LIQUIDITY_EXIT_DROP_PCT", "0.25"))  # 25% below entry liquidity
EMERGENCY_LIQUIDITY_DROP_PCT = float(os.getenv("EMERGENCY_LIQUIDITY_DROP_PCT", "0.35"))  # 35% one-shot danger level

# Momentum collapse exits
VELOCITY_EXIT_RATIO = float(os.getenv("VELOCITY_EXIT_RATIO", "0.35"))  # current velocity < 35% of entry velocity
VELOCITY_MIN_ABS = float(os.getenv("VELOCITY_MIN_ABS", "2.0"))  # or absolute floor
NEG_FLOW_WINDOW_SECONDS = int(os.getenv("NEG_FLOW_WINDOW_SECONDS", "120"))
SELL_PRESSURE_REQUIRED_CHECKS = int(os.getenv("SELL_PRESSURE_REQUIRED_CHECKS", "2"))
MOMENTUM_FAIL_REQUIRED_CHECKS = int(os.getenv("MOMENTUM_FAIL_REQUIRED_CHECKS", "2"))

# Trailing stop
ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
TRAILING_ACTIVATE_PCT = float(os.getenv("TRAILING_ACTIVATE_PCT", "0.20"))  # +20%
TRAILING_DISTANCE_PCT = float(os.getenv("TRAILING_DISTANCE_PCT", "0.10"))  # 10%

# Partial / full exit
SELL_PERCENT = float(os.getenv("SELL_PERCENT", "1.0"))  # 1.0 = 100%, 0.5 = 50%


# ---------------------------
# WEB3
# ---------------------------
if not NODE:
    raise RuntimeError("NODE env var is required.")

w3 = Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Failed to connect to Ethereum node.")

print("Connected to Ethereum node")

ACCOUNT = None
if PRIVATE_KEY:
    try:
        ACCOUNT = w3.eth.account.from_key(PRIVATE_KEY)
        print(f"Loaded purchase wallet: {ACCOUNT.address}")
    except Exception as e:
        raise RuntimeError(f"Failed to load PRIVATE_KEY: {e}")
else:
    print("PRIVATE_KEY not set. Auto-purchase will remain disabled.")


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


def send_raw_value(value: str) -> None:
    clean = (value or "").strip()
    if clean:
        send(clean)


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


def dextools_link(pair: str) -> str:
    return f"https://www.dextools.io/app/en/ether/pair-explorer/{pair}"


def dexscreener_link(pair: str) -> str:
    return f"https://dexscreener.com/ethereum/{pair}"


def format_bool(v: bool) -> str:
    return "ON" if v else "OFF"


def purchases_enabled() -> bool:
    return RUN_PURCHASE == "on" and ACCOUNT is not None


def build_tx_params(wallet_address: str, nonce: int, gas: int, value: int = 0) -> dict:
    return {
        "from": wallet_address,
        "value": value,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
    }


# ---------------------------
# ABIS
# ---------------------------
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
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

ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "outputs": [{"name": "", "type": "uint256[]"}],
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

CHAINLINK_ETH_USD_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

factory_contract = w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)
router_contract = w3.eth.contract(address=UNISWAP_V2_ROUTER, abi=ROUTER_ABI)
eth_usd_contract = w3.eth.contract(address=CHAINLINK_ETH_USD, abi=CHAINLINK_ETH_USD_ABI)


# ---------------------------
# TOKEN / RISK
# ---------------------------
def get_token_contract(token: str):
    return w3.eth.contract(address=token, abi=ERC20_ABI)


def get_token_info(token: str) -> tuple[str, str]:
    try:
        token_contract = get_token_contract(token)
        name = safe_call(lambda: token_contract.functions.name().call(), "Unknown")
        symbol = safe_call(lambda: token_contract.functions.symbol().call(), "Unknown")
        return str(name), str(symbol)
    except Exception:
        return "Unknown", "Unknown"


def get_token_decimals(token: str) -> int:
    try:
        token_contract = get_token_contract(token)
        return int(safe_call(lambda: token_contract.functions.decimals().call(), 18))
    except Exception:
        return 18


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
# LIQUIDITY / PRICE
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


def get_eth_usd_price() -> float:
    data = eth_usd_contract.functions.latestRoundData().call()
    answer = int(data[1])
    return answer / 10**8


def usd_to_eth(usd_amount: float) -> float:
    eth_usd = get_eth_usd_price()
    if eth_usd <= 0:
        raise RuntimeError("Failed to get ETH/USD price.")
    return usd_amount / eth_usd


def token_amount_to_human(raw_amount: int, decimals: int) -> float:
    return raw_amount / (10 ** decimals)


def estimate_eth_out_for_tokens(token: str, amount_in_raw: int) -> float:
    try:
        path = [token, WETH]
        amounts = router_contract.functions.getAmountsOut(int(amount_in_raw), path).call()
        eth_out_wei = int(amounts[-1])
        return float(w3.from_wei(eth_out_wei, "ether"))
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
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount0_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, buyer
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount0_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, seller

    if token1.lower() == WETH.lower():
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount1_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, buyer
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount1_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, seller

    return None, 0.0, None


# ---------------------------
# POSITION STATE
# ---------------------------
PURCHASED_TOKENS = set()
PURCHASE_IN_PROGRESS = set()
PURCHASE_LOCK = threading.Lock()

ACTIVE_PAIRS = set()
ACTIVE_POSITIONS = {}
POSITION_LOCK = threading.Lock()

TOKEN_SELECTION_LOCK = threading.Lock()
TOKEN_SELECTION_STARTED = set()
TOKEN_DECIDED = set()
TOKEN_CANDIDATES = defaultdict(dict)


class Position:
    def __init__(
        self,
        token: str,
        pair: str,
        name: str,
        symbol: str,
        decimals: int,
        entry_liquidity: float,
        entry_velocity: float,
        entry_buy_eth: float,
        entry_sell_eth: float,
        purchased_eth: float,
        token_balance_raw: int,
        tx_hash: str,
    ):
        self.token = token
        self.pair = pair
        self.name = name
        self.symbol = symbol
        self.decimals = decimals
        self.entry_liquidity = entry_liquidity
        self.entry_velocity = entry_velocity
        self.entry_buy_eth = entry_buy_eth
        self.entry_sell_eth = entry_sell_eth
        self.purchased_eth = purchased_eth
        self.token_balance_raw = token_balance_raw
        self.buy_tx_hash = tx_hash
        self.opened_ts = now_ts()
        self.last_block = w3.eth.block_number
        self.trailing_active = False
        self.peak_estimated_eth = purchased_eth
        self.fail_checks_sell_pressure = 0
        self.fail_checks_velocity = 0
        self.closed = False


# ---------------------------
# POOL SELECTION
# ---------------------------
def compute_pool_score(candidate: dict) -> float:
    liquidity = float(candidate.get("liquidity", 0.0))
    buy_eth = float(candidate.get("buy_eth", 0.0))
    sell_eth = float(candidate.get("sell_eth", 0.0))
    buyer_velocity = float(candidate.get("buyer_velocity", 0.0))
    top_buyer_share = float(candidate.get("top_buyer_share", 0.0))

    score = 0.0
    score += liquidity * POOL_SCORE_LIQUIDITY_WEIGHT
    score += buy_eth * POOL_SCORE_BUY_ETH_WEIGHT
    score += buyer_velocity * POOL_SCORE_VELOCITY_WEIGHT
    score -= sell_eth * POOL_SCORE_SELL_PENALTY_WEIGHT
    score -= top_buyer_share * 100.0 * POOL_SCORE_TOP_BUYER_PENALTY_WEIGHT
    return score


def build_signal_message(best: dict, token: str) -> str:
    mode = "🧪 WOULD BUY" if not purchases_enabled() else "🟢 BUY SIGNAL PASSED"

    return (
        f"{mode}\n\n"
        f"{best['name']} ({best['symbol']})\n\n"
        f"Liquidity: {best['liquidity']:.2f} ETH\n"
        f"Buys: {best['buy_count']}\n"
        f"Sells: {best['sell_count']}\n"
        f"Unique buyers: {best['unique_buyer_count']}\n"
        f"Unique sellers: {best['unique_seller_count']}\n"
        f"Buy ETH: {best['buy_eth']:.2f}\n"
        f"Sell ETH: {best['sell_eth']:.2f}\n"
        f"Buyer velocity: {best['buyer_velocity']:.2f}/min\n"
        f"Top buyer share: {best['top_buyer_share']:.0%}\n"
        f"Pool score: {best.get('pool_score', 0.0):.2f}\n"
        f"Sellability: {'PASS' if best['sell_count'] >= 1 else 'FAIL'}\n"
        f"Risk check: {best['risk_reason']}\n"
        f"Run purchase: {format_bool(purchases_enabled())}\n"
        f"Purchase USD: ${PURCHASE_AMOUNT_USD:.2f}\n\n"
        f"Pair\n{best['pair']}\n\n"
        f"Token\n{token}\n\n"
        "DexTools\n"
        f"{dextools_link(best['pair'])}\n\n"
        "DexScreener\n"
        f"{dexscreener_link(best['pair'])}"
    )


def finalize_token_selection(token: str) -> None:
    time.sleep(POOL_SELECTION_WINDOW_SECONDS)

    try:
        with TOKEN_SELECTION_LOCK:
            if token in TOKEN_DECIDED:
                return
            candidates = list(TOKEN_CANDIDATES.get(token, {}).values())
            if not candidates:
                TOKEN_SELECTION_STARTED.discard(token)
                return

        best = None
        best_score = None
        for candidate in candidates:
            score = compute_pool_score(candidate)
            candidate["pool_score"] = score
            if best is None or score > best_score:
                best = candidate
                best_score = score

        if best is None:
            return

        if best_score is not None and best_score < POOL_SELECTION_MIN_SCORE:
            return

        with TOKEN_SELECTION_LOCK:
            TOKEN_DECIDED.add(token)

        # Alert only after the coin fully passes all buy criteria.
        send(build_signal_message(best, token))
        send_raw_value(token)

        # Only place live buy if enabled.
        if purchases_enabled():
            execute_purchase(
                token=token,
                pair=best["pair"],
                name=best["name"],
                symbol=best["symbol"],
                entry_liquidity=best["liquidity"],
                entry_velocity=best["buyer_velocity"],
                entry_buy_eth=best["buy_eth"],
                entry_sell_eth=best["sell_eth"],
            )

    finally:
        with TOKEN_SELECTION_LOCK:
            TOKEN_SELECTION_STARTED.discard(token)
            TOKEN_CANDIDATES.pop(token, None)


# ---------------------------
# AUTO BUY / APPROVE / SELL
# ---------------------------
def approve_token_if_needed(token: str, amount_raw: int, name: str, symbol: str) -> bool:
    token_contract = get_token_contract(token)
    wallet_address = ACCOUNT.address

    try:
        allowance = int(token_contract.functions.allowance(wallet_address, UNISWAP_V2_ROUTER).call())
        if allowance >= amount_raw:
            return True

        nonce = w3.eth.get_transaction_count(wallet_address, "pending")
        tx = token_contract.functions.approve(
            UNISWAP_V2_ROUTER,
            2**256 - 1,
        ).build_transaction(
            build_tx_params(wallet_address, nonce, GAS_LIMIT_APPROVE)
        )

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        send(
            "🟡 APPROVE SUBMITTED\n\n"
            f"{name} ({symbol})\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status == 1:
            send(
                "✅ APPROVE COMPLETED\n\n"
                f"{name} ({symbol})\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )
            return True

        send(
            "❌ APPROVE FAILED\n\n"
            f"{name} ({symbol})\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )
        return False

    except Exception as e:
        send(
            "❌ APPROVE ERROR\n\n"
            f"{name} ({symbol})\n"
            f"Reason: {e}"
        )
        return False


def execute_purchase(
    token: str,
    pair: str,
    name: str,
    symbol: str,
    entry_liquidity: float,
    entry_velocity: float,
    entry_buy_eth: float,
    entry_sell_eth: float,
) -> None:
    if not purchases_enabled():
        return

    with PURCHASE_LOCK:
        if token in PURCHASED_TOKENS or token in PURCHASE_IN_PROGRESS:
            return
        PURCHASE_IN_PROGRESS.add(token)

    try:
        eth_amount = usd_to_eth(PURCHASE_AMOUNT_USD)
        value_wei = int(w3.to_wei(eth_amount, "ether"))
        path = [WETH, token]

        wallet_address = ACCOUNT.address
        decimals = get_token_decimals(token)

        balance_before = int(get_token_contract(token).functions.balanceOf(wallet_address).call())

        nonce = w3.eth.get_transaction_count(wallet_address, "pending")
        deadline = int(time.time()) + BUY_DEADLINE_SECONDS

        try:
            amounts_out = router_contract.functions.getAmountsOut(value_wei, path).call()
            expected_out = int(amounts_out[-1])
            amount_out_min = int(expected_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception:
            amount_out_min = 0

        tx = router_contract.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            amount_out_min,
            path,
            wallet_address,
            deadline,
        ).build_transaction(
            build_tx_params(wallet_address, nonce, GAS_LIMIT_BUY, value=value_wei)
        )

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        send(
            "🟢 AUTO BUY SUBMITTED\n\n"
            f"{name} ({symbol})\n"
            f"USD amount: ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH: {eth_amount:.6f}\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status != 1:
            send(
                "❌ PURCHASE FAILED\n\n"
                f"{name} ({symbol})\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )
            return

        balance_after = int(get_token_contract(token).functions.balanceOf(wallet_address).call())
        token_balance_raw = max(balance_after - balance_before, 0)

        send(
            "✅ PURCHASE COMPLETED\n\n"
            f"{name} ({symbol})\n"
            f"USD amount: ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH: {eth_amount:.6f}\n"
            f"Received tokens: {token_amount_to_human(token_balance_raw, decimals):,.6f}\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        send_raw_value(token)

        if token_balance_raw <= 0:
            send(
                "⚠️ POSITION NOT STARTED\n\n"
                f"{name} ({symbol})\n"
                "Reason: token balance after buy was zero"
            )
            return

        if not approve_token_if_needed(token, token_balance_raw, name, symbol):
            return

        with PURCHASE_LOCK:
            PURCHASED_TOKENS.add(token)

        if ENABLE_POSITION_MONITOR:
            position = Position(
                token=token,
                pair=pair,
                name=name,
                symbol=symbol,
                decimals=decimals,
                entry_liquidity=entry_liquidity,
                entry_velocity=entry_velocity,
                entry_buy_eth=entry_buy_eth,
                entry_sell_eth=entry_sell_eth,
                purchased_eth=eth_amount,
                token_balance_raw=token_balance_raw,
                tx_hash=tx_hex,
            )
            with POSITION_LOCK:
                ACTIVE_POSITIONS[token] = position

            threading.Thread(target=monitor_position, args=(position,), daemon=True).start()

    except Exception as e:
        send(
            "❌ PURCHASE ERROR\n\n"
            f"{name} ({symbol})\n"
            f"Reason: {e}"
        )
    finally:
        with PURCHASE_LOCK:
            PURCHASE_IN_PROGRESS.discard(token)


def execute_sell(position: Position, reason: str) -> None:
    if position.closed:
        return
    if not purchases_enabled():
        return

    with POSITION_LOCK:
        if position.closed:
            return
        position.closed = True

    try:
        token_contract = get_token_contract(position.token)
        wallet_address = ACCOUNT.address
        current_balance_raw = int(token_contract.functions.balanceOf(wallet_address).call())

        if current_balance_raw <= 0:
            send(
                "⚠️ SELL SKIPPED\n\n"
                f"{position.name} ({position.symbol})\n"
                "Reason: token balance is zero"
            )
            return

        amount_in = int(current_balance_raw * SELL_PERCENT)
        if amount_in <= 0:
            send(
                "⚠️ SELL SKIPPED\n\n"
                f"{position.name} ({position.symbol})\n"
                "Reason: computed sell amount is zero"
            )
            return

        if not approve_token_if_needed(position.token, amount_in, position.name, position.symbol):
            return

        deadline = int(time.time()) + SELL_DEADLINE_SECONDS
        path = [position.token, WETH]

        try:
            amounts_out = router_contract.functions.getAmountsOut(amount_in, path).call()
            expected_eth_out = int(amounts_out[-1])
            amount_out_min = int(expected_eth_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception:
            expected_eth_out = 0
            amount_out_min = 0

        nonce = w3.eth.get_transaction_count(wallet_address, "pending")
        tx = router_contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            amount_in,
            amount_out_min,
            path,
            wallet_address,
            deadline,
        ).build_transaction(
            build_tx_params(wallet_address, nonce, GAS_LIMIT_SELL)
        )

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        send(
            "🔴 AUTO SELL SUBMITTED\n\n"
            f"{position.name} ({position.symbol})\n"
            f"Reason: {reason}\n"
            f"Sell percent: {SELL_PERCENT:.0%}\n"
            f"Estimated ETH out: {float(w3.from_wei(expected_eth_out, 'ether')):.6f}\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status == 1:
            send(
                "✅ AUTO SELL COMPLETED\n\n"
                f"{position.name} ({position.symbol})\n"
                f"Reason: {reason}\n"
                f"Sell percent: {SELL_PERCENT:.0%}\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )
        else:
            send(
                "❌ AUTO SELL FAILED\n\n"
                f"{position.name} ({position.symbol})\n"
                f"Reason: {reason}\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )

    except Exception as e:
        send(
            "❌ AUTO SELL ERROR\n\n"
            f"{position.name} ({position.symbol})\n"
            f"Reason: {reason}\n"
            f"Error: {e}"
        )
    finally:
        with POSITION_LOCK:
            ACTIVE_POSITIONS.pop(position.token, None)


# ---------------------------
# POSITION MONITOR
# ---------------------------
def monitor_position(position: Position) -> None:
    send(
        "🛡️ POSITION MONITOR STARTED\n\n"
        f"{position.name} ({position.symbol})\n"
        f"Entry liquidity: {position.entry_liquidity:.4f} ETH\n"
        f"Entry velocity: {position.entry_velocity:.2f}/min\n"
        f"Buy ETH at signal: {position.entry_buy_eth:.4f}\n"
        f"Sell ETH at signal: {position.entry_sell_eth:.4f}\n"
        f"Trailing stop: {format_bool(ENABLE_TRAILING_STOP)}"
    )

    pair_contract = get_pair_contract(position.pair)
    token0 = pair_contract.functions.token0().call()
    token1 = pair_contract.functions.token1().call()

    recent_events = deque()

    while not position.closed:
        try:
            current_liquidity = check_liquidity_eth(position.pair)

            current_block = w3.eth.block_number
            if current_block >= position.last_block:
                try:
                    events = pair_contract.events.Swap.get_logs(
                        from_block=position.last_block,
                        to_block=current_block,
                    )
                    position.last_block = current_block + 1
                except Exception:
                    events = []

                for event in events:
                    side, eth_amount, wallet = parse_swap_direction(event["args"], token0, token1)
                    recent_events.append((now_ts(), side, eth_amount, str(wallet) if wallet else ""))

            cutoff = now_ts() - NEG_FLOW_WINDOW_SECONDS
            while recent_events and recent_events[0][0] < cutoff:
                recent_events.popleft()

            buy_eth_window = 0.0
            sell_eth_window = 0.0
            window_unique_buyers = set()

            for ts, side, eth_amount, wallet in recent_events:
                if side == "buy":
                    buy_eth_window += eth_amount
                    if wallet:
                        window_unique_buyers.add(wallet)
                elif side == "sell":
                    sell_eth_window += eth_amount

            elapsed_window_minutes = max(NEG_FLOW_WINDOW_SECONDS / 60.0, 0.01)
            current_velocity = len(window_unique_buyers) / elapsed_window_minutes

            wallet_balance_raw = int(get_token_contract(position.token).functions.balanceOf(ACCOUNT.address).call())
            if wallet_balance_raw <= 0:
                send(
                    "ℹ️ POSITION CLOSED\n\n"
                    f"{position.name} ({position.symbol})\n"
                    "Wallet balance is zero, stopping monitor."
                )
                break

            estimated_eth_now = estimate_eth_out_for_tokens(position.token, int(wallet_balance_raw * SELL_PERCENT))
            if estimated_eth_now > position.peak_estimated_eth:
                position.peak_estimated_eth = estimated_eth_now

            pnl_pct = 0.0
            if position.purchased_eth > 0:
                pnl_pct = (estimated_eth_now / position.purchased_eth) - 1.0

            if ENABLE_TRAILING_STOP and not position.trailing_active and pnl_pct >= TRAILING_ACTIVATE_PCT:
                position.trailing_active = True
                send(
                    "📈 TRAILING STOP ACTIVATED\n\n"
                    f"{position.name} ({position.symbol})\n"
                    f"Estimated PnL: {pnl_pct * 100:.2f}%\n"
                    f"Peak est ETH: {position.peak_estimated_eth:.6f}"
                )

            if position.trailing_active and position.peak_estimated_eth > 0:
                trail_floor = position.peak_estimated_eth * (1.0 - TRAILING_DISTANCE_PCT)
                if estimated_eth_now < trail_floor:
                    execute_sell(
                        position,
                        f"trailing stop hit | est_eth={estimated_eth_now:.6f} peak={position.peak_estimated_eth:.6f}",
                    )
                    break

            liquidity_floor = position.entry_liquidity * (1.0 - LIQUIDITY_EXIT_DROP_PCT)
            if current_liquidity > 0 and current_liquidity < liquidity_floor:
                execute_sell(
                    position,
                    f"liquidity dropped below floor | current={current_liquidity:.4f} entry={position.entry_liquidity:.4f}",
                )
                break

            emergency_floor = position.entry_liquidity * (1.0 - EMERGENCY_LIQUIDITY_DROP_PCT)
            if current_liquidity > 0 and current_liquidity < emergency_floor:
                execute_sell(
                    position,
                    f"emergency liquidity drain | current={current_liquidity:.4f} entry={position.entry_liquidity:.4f}",
                )
                break

            if sell_eth_window > buy_eth_window and sell_eth_window > 0:
                position.fail_checks_sell_pressure += 1
            else:
                position.fail_checks_sell_pressure = 0

            if position.fail_checks_sell_pressure >= SELL_PRESSURE_REQUIRED_CHECKS:
                execute_sell(
                    position,
                    f"net flow negative | buy_eth={buy_eth_window:.4f} sell_eth={sell_eth_window:.4f}",
                )
                break

            velocity_threshold = max(position.entry_velocity * VELOCITY_EXIT_RATIO, VELOCITY_MIN_ABS)
            if current_velocity < velocity_threshold:
                position.fail_checks_velocity += 1
            else:
                position.fail_checks_velocity = 0

            if position.fail_checks_velocity >= MOMENTUM_FAIL_REQUIRED_CHECKS and sell_eth_window >= buy_eth_window:
                execute_sell(
                    position,
                    f"buyer velocity collapsed | current={current_velocity:.2f}/min threshold={velocity_threshold:.2f}/min",
                )
                break

            time.sleep(POSITION_CHECK_SECONDS)

        except Exception as e:
            print(f"Position monitor error for {position.symbol}: {e}")
            time.sleep(POSITION_CHECK_SECONDS)

    with POSITION_LOCK:
        ACTIVE_POSITIONS.pop(position.token, None)


# ---------------------------
# CORE TRACKER
# ---------------------------
def process_new_token(token: str, pair: str) -> None:
    if pair in ACTIVE_PAIRS:
        return

    ACTIVE_PAIRS.add(pair)

    def worker():
        try:
            pair_contract = get_pair_contract(pair)
            token0 = pair_contract.functions.token0().call()
            token1 = pair_contract.functions.token1().call()

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

            candidate = {
                "token": token,
                "pair": pair,
                "name": name,
                "symbol": symbol,
                "liquidity": liquidity,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "buy_eth": buy_eth,
                "sell_eth": sell_eth,
                "unique_buyer_count": unique_buyer_count,
                "unique_seller_count": len(unique_sellers),
                "buyer_velocity": buyer_velocity,
                "top_buyer_share": top_buyer_share,
                "risk_reason": risk_reason,
            }

            if ONLY_ALERT_BEST_POOL_PER_TOKEN:
                with TOKEN_SELECTION_LOCK:
                    TOKEN_CANDIDATES[token][pair] = candidate
                    if token not in TOKEN_SELECTION_STARTED and token not in TOKEN_DECIDED:
                        TOKEN_SELECTION_STARTED.add(token)
                        threading.Thread(target=finalize_token_selection, args=(token,), daemon=True).start()
            else:
                candidate["pool_score"] = compute_pool_score(candidate)
                if candidate["pool_score"] < POOL_SELECTION_MIN_SCORE:
                    return

                send(build_signal_message(candidate, token))
                send_raw_value(token)

                if purchases_enabled():
                    execute_purchase(
                        token=token,
                        pair=pair,
                        name=name,
                        symbol=symbol,
                        entry_liquidity=liquidity,
                        entry_velocity=buyer_velocity,
                        entry_buy_eth=buy_eth,
                        entry_sell_eth=sell_eth,
                    )

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
    send(
        "ETH early-run scanner started\n"
        f"Run purchase: {format_bool(purchases_enabled())}\n"
        f"Purchase USD: ${PURCHASE_AMOUNT_USD:.2f}\n"
        f"Position monitor: {format_bool(ENABLE_POSITION_MONITOR)}\n"
        f"Best-pool mode: {format_bool(ONLY_ALERT_BEST_POOL_PER_TOKEN)}\n"
        f"Pool selection window: {POOL_SELECTION_WINDOW_SECONDS}s\n"
        f"Liquidity exit drop: {LIQUIDITY_EXIT_DROP_PCT:.0%}\n"
        f"Trailing stop: {format_bool(ENABLE_TRAILING_STOP)}\n"
        "Alert mode: ONLY coins that fully pass buy criteria"
    )

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
from collections import defaultdict, deque


# ---------------------------
# CONFIG
# ---------------------------
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Wallet / auto-purchase
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
RUN_PURCHASE = os.getenv("RUN_PURCHASE", "off").strip().lower()  # on / off
PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD", "50"))
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "1500"))  # 1500 = 15%
GAS_LIMIT_BUY = int(os.getenv("GAS_LIMIT_BUY", "450000"))
GAS_LIMIT_APPROVE = int(os.getenv("GAS_LIMIT_APPROVE", "120000"))
GAS_LIMIT_SELL = int(os.getenv("GAS_LIMIT_SELL", "450000"))
BUY_DEADLINE_SECONDS = int(os.getenv("BUY_DEADLINE_SECONDS", "180"))
SELL_DEADLINE_SECONDS = int(os.getenv("SELL_DEADLINE_SECONDS", "180"))

WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
UNISWAP_V2_ROUTER = Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
CHAINLINK_ETH_USD = Web3.to_checksum_address("0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419")

# Binary-style early runner filters
MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY", "3.0"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY", "25"))

LIQUIDITY_WAIT_SECONDS = int(os.getenv("LIQUIDITY_WAIT_SECONDS", "120"))
TRACK_SECONDS = int(os.getenv("TRACK_SECONDS", "90"))

MONEY_MIN_BUYS = int(os.getenv("MONEY_MIN_BUYS", "5"))
MONEY_MIN_UNIQUE_BUYERS = int(os.getenv("MONEY_MIN_UNIQUE_BUYERS", "5"))
MONEY_MIN_BUY_ETH = float(os.getenv("MONEY_MIN_BUY_ETH", "1.0"))
MONEY_MIN_BUYER_VELOCITY = float(os.getenv("MONEY_MIN_BUYER_VELOCITY", "3.0"))  # unique buyers per minute
REQUIRE_ONE_SUCCESSFUL_SELL = os.getenv("REQUIRE_ONE_SUCCESSFUL_SELL", "true").lower() == "true"

# Filter to avoid obvious single-wallet bursts
MAX_TOP_BUYER_SHARE = float(os.getenv("MAX_TOP_BUYER_SHARE", "0.45"))

BLOCK_POLL_SECONDS = float(os.getenv("BLOCK_POLL_SECONDS", "2"))
PAIR_POLL_SECONDS = float(os.getenv("PAIR_POLL_SECONDS", "1.5"))

# ---------------------------
# TOKEN / POOL SELECTION CONFIG
# ---------------------------
ONLY_ALERT_BEST_POOL_PER_TOKEN = os.getenv("ONLY_ALERT_BEST_POOL_PER_TOKEN", "true").lower() == "true"
POOL_SELECTION_WINDOW_SECONDS = int(os.getenv("POOL_SELECTION_WINDOW_SECONDS", "45"))
POOL_SCORE_LIQUIDITY_WEIGHT = float(os.getenv("POOL_SCORE_LIQUIDITY_WEIGHT", "1.0"))
POOL_SCORE_BUY_ETH_WEIGHT = float(os.getenv("POOL_SCORE_BUY_ETH_WEIGHT", "1.4"))
POOL_SCORE_VELOCITY_WEIGHT = float(os.getenv("POOL_SCORE_VELOCITY_WEIGHT", "1.2"))
POOL_SCORE_SELL_PENALTY_WEIGHT = float(os.getenv("POOL_SCORE_SELL_PENALTY_WEIGHT", "1.2"))
POOL_SCORE_TOP_BUYER_PENALTY_WEIGHT = float(os.getenv("POOL_SCORE_TOP_BUYER_PENALTY_WEIGHT", "1.0"))
POOL_SELECTION_MIN_SCORE = float(os.getenv("POOL_SELECTION_MIN_SCORE", "0"))

# ---------------------------
# AUTO-SELL / DEFENSE CONFIG
# ---------------------------
ENABLE_POSITION_MONITOR = os.getenv("ENABLE_POSITION_MONITOR", "true").lower() == "true"
POSITION_CHECK_SECONDS = float(os.getenv("POSITION_CHECK_SECONDS", "3"))

# Liquidity-based exits
LIQUIDITY_EXIT_DROP_PCT = float(os.getenv("LIQUIDITY_EXIT_DROP_PCT", "0.25"))  # 25% below entry liquidity
EMERGENCY_LIQUIDITY_DROP_PCT = float(os.getenv("EMERGENCY_LIQUIDITY_DROP_PCT", "0.35"))  # 35% one-shot danger level

# Momentum collapse exits
VELOCITY_EXIT_RATIO = float(os.getenv("VELOCITY_EXIT_RATIO", "0.35"))  # current velocity < 35% of entry velocity
VELOCITY_MIN_ABS = float(os.getenv("VELOCITY_MIN_ABS", "2.0"))  # or absolute floor
NEG_FLOW_WINDOW_SECONDS = int(os.getenv("NEG_FLOW_WINDOW_SECONDS", "120"))
SELL_PRESSURE_REQUIRED_CHECKS = int(os.getenv("SELL_PRESSURE_REQUIRED_CHECKS", "2"))
MOMENTUM_FAIL_REQUIRED_CHECKS = int(os.getenv("MOMENTUM_FAIL_REQUIRED_CHECKS", "2"))

# Trailing stop
ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
TRAILING_ACTIVATE_PCT = float(os.getenv("TRAILING_ACTIVATE_PCT", "0.20"))  # +20%
TRAILING_DISTANCE_PCT = float(os.getenv("TRAILING_DISTANCE_PCT", "0.10"))  # 10%

# Partial / full exit
SELL_PERCENT = float(os.getenv("SELL_PERCENT", "1.0"))  # 1.0 = 100%, 0.5 = 50%


# ---------------------------
# WEB3
# ---------------------------
if not NODE:
    raise RuntimeError("NODE env var is required.")

w3 = Web3(Web3.HTTPProvider(NODE))

if not w3.is_connected():
    raise RuntimeError("Failed to connect to Ethereum node.")

print("Connected to Ethereum node")

ACCOUNT = None
if PRIVATE_KEY:
    try:
        ACCOUNT = w3.eth.account.from_key(PRIVATE_KEY)
        print(f"Loaded purchase wallet: {ACCOUNT.address}")
    except Exception as e:
        raise RuntimeError(f"Failed to load PRIVATE_KEY: {e}")
else:
    print("PRIVATE_KEY not set. Auto-purchase will remain disabled.")


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


def send_raw_value(value: str) -> None:
    clean = (value or "").strip()
    if clean:
        send(clean)


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


def dextools_link(pair: str) -> str:
    return f"https://www.dextools.io/app/en/ether/pair-explorer/{pair}"


def dexscreener_link(pair: str) -> str:
    return f"https://dexscreener.com/ethereum/{pair}"


def format_bool(v: bool) -> str:
    return "ON" if v else "OFF"


def purchases_enabled() -> bool:
    return RUN_PURCHASE == "on" and ACCOUNT is not None


def build_tx_params(wallet_address: str, nonce: int, gas: int, value: int = 0) -> dict:
    return {
        "from": wallet_address,
        "value": value,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
    }


# ---------------------------
# ABIS
# ---------------------------
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
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

ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "outputs": [{"name": "", "type": "uint256[]"}],
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

CHAINLINK_ETH_USD_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

factory_contract = w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)
router_contract = w3.eth.contract(address=UNISWAP_V2_ROUTER, abi=ROUTER_ABI)
eth_usd_contract = w3.eth.contract(address=CHAINLINK_ETH_USD, abi=CHAINLINK_ETH_USD_ABI)


# ---------------------------
# TOKEN / RISK
# ---------------------------
def get_token_contract(token: str):
    return w3.eth.contract(address=token, abi=ERC20_ABI)


def get_token_info(token: str) -> tuple[str, str]:
    try:
        token_contract = get_token_contract(token)
        name = safe_call(lambda: token_contract.functions.name().call(), "Unknown")
        symbol = safe_call(lambda: token_contract.functions.symbol().call(), "Unknown")
        return str(name), str(symbol)
    except Exception:
        return "Unknown", "Unknown"


def get_token_decimals(token: str) -> int:
    try:
        token_contract = get_token_contract(token)
        return int(safe_call(lambda: token_contract.functions.decimals().call(), 18))
    except Exception:
        return 18


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
# LIQUIDITY / PRICE
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


def get_eth_usd_price() -> float:
    data = eth_usd_contract.functions.latestRoundData().call()
    answer = int(data[1])
    return answer / 10**8


def usd_to_eth(usd_amount: float) -> float:
    eth_usd = get_eth_usd_price()
    if eth_usd <= 0:
        raise RuntimeError("Failed to get ETH/USD price.")
    return usd_amount / eth_usd


def token_amount_to_human(raw_amount: int, decimals: int) -> float:
    return raw_amount / (10 ** decimals)


def estimate_eth_out_for_tokens(token: str, amount_in_raw: int) -> float:
    try:
        path = [token, WETH]
        amounts = router_contract.functions.getAmountsOut(int(amount_in_raw), path).call()
        eth_out_wei = int(amounts[-1])
        return float(w3.from_wei(eth_out_wei, "ether"))
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
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount0_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, buyer
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount0_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, seller

    if token1.lower() == WETH.lower():
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount1_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, buyer
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount1_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, seller

    return None, 0.0, None


# ---------------------------
# POSITION STATE
# ---------------------------
PURCHASED_TOKENS = set()
PURCHASE_IN_PROGRESS = set()
PURCHASE_LOCK = threading.Lock()

ACTIVE_PAIRS = set()
ACTIVE_POSITIONS = {}
POSITION_LOCK = threading.Lock()

TOKEN_SELECTION_LOCK = threading.Lock()
TOKEN_SELECTION_STARTED = set()
TOKEN_DECIDED = set()
TOKEN_CANDIDATES = defaultdict(dict)


class Position:
    def __init__(
        self,
        token: str,
        pair: str,
        name: str,
        symbol: str,
        decimals: int,
        entry_liquidity: float,
        entry_velocity: float,
        entry_buy_eth: float,
        entry_sell_eth: float,
        purchased_eth: float,
        token_balance_raw: int,
        tx_hash: str,
    ):
        self.token = token
        self.pair = pair
        self.name = name
        self.symbol = symbol
        self.decimals = decimals
        self.entry_liquidity = entry_liquidity
        self.entry_velocity = entry_velocity
        self.entry_buy_eth = entry_buy_eth
        self.entry_sell_eth = entry_sell_eth
        self.purchased_eth = purchased_eth
        self.token_balance_raw = token_balance_raw
        self.buy_tx_hash = tx_hash
        self.opened_ts = now_ts()
        self.last_block = w3.eth.block_number
        self.trailing_active = False
        self.peak_estimated_eth = purchased_eth
        self.fail_checks_sell_pressure = 0
        self.fail_checks_velocity = 0
        self.closed = False


# ---------------------------
# POOL SELECTION
# ---------------------------
def compute_pool_score(candidate: dict) -> float:
    liquidity = float(candidate.get("liquidity", 0.0))
    buy_eth = float(candidate.get("buy_eth", 0.0))
    sell_eth = float(candidate.get("sell_eth", 0.0))
    buyer_velocity = float(candidate.get("buyer_velocity", 0.0))
    top_buyer_share = float(candidate.get("top_buyer_share", 0.0))

    score = 0.0
    score += liquidity * POOL_SCORE_LIQUIDITY_WEIGHT
    score += buy_eth * POOL_SCORE_BUY_ETH_WEIGHT
    score += buyer_velocity * POOL_SCORE_VELOCITY_WEIGHT
    score -= sell_eth * POOL_SCORE_SELL_PENALTY_WEIGHT
    score -= top_buyer_share * 100.0 * POOL_SCORE_TOP_BUYER_PENALTY_WEIGHT
    return score


def build_signal_message(best: dict, token: str) -> str:
    mode = "🧪 WOULD BUY" if not purchases_enabled() else "🟢 BUY SIGNAL PASSED"

    return (
        f"{mode}\n\n"
        f"{best['name']} ({best['symbol']})\n\n"
        f"Liquidity: {best['liquidity']:.2f} ETH\n"
        f"Buys: {best['buy_count']}\n"
        f"Sells: {best['sell_count']}\n"
        f"Unique buyers: {best['unique_buyer_count']}\n"
        f"Unique sellers: {best['unique_seller_count']}\n"
        f"Buy ETH: {best['buy_eth']:.2f}\n"
        f"Sell ETH: {best['sell_eth']:.2f}\n"
        f"Buyer velocity: {best['buyer_velocity']:.2f}/min\n"
        f"Top buyer share: {best['top_buyer_share']:.0%}\n"
        f"Pool score: {best.get('pool_score', 0.0):.2f}\n"
        f"Sellability: {'PASS' if best['sell_count'] >= 1 else 'FAIL'}\n"
        f"Risk check: {best['risk_reason']}\n"
        f"Run purchase: {format_bool(purchases_enabled())}\n"
        f"Purchase USD: ${PURCHASE_AMOUNT_USD:.2f}\n\n"
        f"Pair\n{best['pair']}\n\n"
        f"Token\n{token}\n\n"
        "DexTools\n"
        f"{dextools_link(best['pair'])}\n\n"
        "DexScreener\n"
        f"{dexscreener_link(best['pair'])}"
    )


def finalize_token_selection(token: str) -> None:
    time.sleep(POOL_SELECTION_WINDOW_SECONDS)

    try:
        with TOKEN_SELECTION_LOCK:
            if token in TOKEN_DECIDED:
                return
            candidates = list(TOKEN_CANDIDATES.get(token, {}).values())
            if not candidates:
                TOKEN_SELECTION_STARTED.discard(token)
                return

        best = None
        best_score = None
        for candidate in candidates:
            score = compute_pool_score(candidate)
            candidate["pool_score"] = score
            if best is None or score > best_score:
                best = candidate
                best_score = score

        if best is None:
            return

        if best_score is not None and best_score < POOL_SELECTION_MIN_SCORE:
            return

        with TOKEN_SELECTION_LOCK:
            TOKEN_DECIDED.add(token)

        # Alert only after the coin fully passes all buy criteria.
        send(build_signal_message(best, token))
        send_raw_value(token)

        # Only place live buy if enabled.
        if purchases_enabled():
            execute_purchase(
                token=token,
                pair=best["pair"],
                name=best["name"],
                symbol=best["symbol"],
                entry_liquidity=best["liquidity"],
                entry_velocity=best["buyer_velocity"],
                entry_buy_eth=best["buy_eth"],
                entry_sell_eth=best["sell_eth"],
            )

    finally:
        with TOKEN_SELECTION_LOCK:
            TOKEN_SELECTION_STARTED.discard(token)
            TOKEN_CANDIDATES.pop(token, None)


# ---------------------------
# AUTO BUY / APPROVE / SELL
# ---------------------------
def approve_token_if_needed(token: str, amount_raw: int, name: str, symbol: str) -> bool:
    token_contract = get_token_contract(token)
    wallet_address = ACCOUNT.address

    try:
        allowance = int(token_contract.functions.allowance(wallet_address, UNISWAP_V2_ROUTER).call())
        if allowance >= amount_raw:
            return True

        nonce = w3.eth.get_transaction_count(wallet_address, "pending")
        tx = token_contract.functions.approve(
            UNISWAP_V2_ROUTER,
            2**256 - 1,
        ).build_transaction(
            build_tx_params(wallet_address, nonce, GAS_LIMIT_APPROVE)
        )

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        send(
            "🟡 APPROVE SUBMITTED\n\n"
            f"{name} ({symbol})\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status == 1:
            send(
                "✅ APPROVE COMPLETED\n\n"
                f"{name} ({symbol})\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )
            return True

        send(
            "❌ APPROVE FAILED\n\n"
            f"{name} ({symbol})\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )
        return False

    except Exception as e:
        send(
            "❌ APPROVE ERROR\n\n"
            f"{name} ({symbol})\n"
            f"Reason: {e}"
        )
        return False


def execute_purchase(
    token: str,
    pair: str,
    name: str,
    symbol: str,
    entry_liquidity: float,
    entry_velocity: float,
    entry_buy_eth: float,
    entry_sell_eth: float,
) -> None:
    if not purchases_enabled():
        return

    with PURCHASE_LOCK:
        if token in PURCHASED_TOKENS or token in PURCHASE_IN_PROGRESS:
            return
        PURCHASE_IN_PROGRESS.add(token)

    try:
        eth_amount = usd_to_eth(PURCHASE_AMOUNT_USD)
        value_wei = int(w3.to_wei(eth_amount, "ether"))
        path = [WETH, token]

        wallet_address = ACCOUNT.address
        decimals = get_token_decimals(token)

        balance_before = int(get_token_contract(token).functions.balanceOf(wallet_address).call())

        nonce = w3.eth.get_transaction_count(wallet_address, "pending")
        deadline = int(time.time()) + BUY_DEADLINE_SECONDS

        try:
            amounts_out = router_contract.functions.getAmountsOut(value_wei, path).call()
            expected_out = int(amounts_out[-1])
            amount_out_min = int(expected_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception:
            amount_out_min = 0

        tx = router_contract.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            amount_out_min,
            path,
            wallet_address,
            deadline,
        ).build_transaction(
            build_tx_params(wallet_address, nonce, GAS_LIMIT_BUY, value=value_wei)
        )

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        send(
            "🟢 AUTO BUY SUBMITTED\n\n"
            f"{name} ({symbol})\n"
            f"USD amount: ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH: {eth_amount:.6f}\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status != 1:
            send(
                "❌ PURCHASE FAILED\n\n"
                f"{name} ({symbol})\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )
            return

        balance_after = int(get_token_contract(token).functions.balanceOf(wallet_address).call())
        token_balance_raw = max(balance_after - balance_before, 0)

        send(
            "✅ PURCHASE COMPLETED\n\n"
            f"{name} ({symbol})\n"
            f"USD amount: ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH: {eth_amount:.6f}\n"
            f"Received tokens: {token_amount_to_human(token_balance_raw, decimals):,.6f}\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        send_raw_value(token)

        if token_balance_raw <= 0:
            send(
                "⚠️ POSITION NOT STARTED\n\n"
                f"{name} ({symbol})\n"
                "Reason: token balance after buy was zero"
            )
            return

        if not approve_token_if_needed(token, token_balance_raw, name, symbol):
            return

        with PURCHASE_LOCK:
            PURCHASED_TOKENS.add(token)

        if ENABLE_POSITION_MONITOR:
            position = Position(
                token=token,
                pair=pair,
                name=name,
                symbol=symbol,
                decimals=decimals,
                entry_liquidity=entry_liquidity,
                entry_velocity=entry_velocity,
                entry_buy_eth=entry_buy_eth,
                entry_sell_eth=entry_sell_eth,
                purchased_eth=eth_amount,
                token_balance_raw=token_balance_raw,
                tx_hash=tx_hex,
            )
            with POSITION_LOCK:
                ACTIVE_POSITIONS[token] = position

            threading.Thread(target=monitor_position, args=(position,), daemon=True).start()

    except Exception as e:
        send(
            "❌ PURCHASE ERROR\n\n"
            f"{name} ({symbol})\n"
            f"Reason: {e}"
        )
    finally:
        with PURCHASE_LOCK:
            PURCHASE_IN_PROGRESS.discard(token)


def execute_sell(position: Position, reason: str) -> None:
    if position.closed:
        return
    if not purchases_enabled():
        return

    with POSITION_LOCK:
        if position.closed:
            return
        position.closed = True

    try:
        token_contract = get_token_contract(position.token)
        wallet_address = ACCOUNT.address
        current_balance_raw = int(token_contract.functions.balanceOf(wallet_address).call())

        if current_balance_raw <= 0:
            send(
                "⚠️ SELL SKIPPED\n\n"
                f"{position.name} ({position.symbol})\n"
                "Reason: token balance is zero"
            )
            return

        amount_in = int(current_balance_raw * SELL_PERCENT)
        if amount_in <= 0:
            send(
                "⚠️ SELL SKIPPED\n\n"
                f"{position.name} ({position.symbol})\n"
                "Reason: computed sell amount is zero"
            )
            return

        if not approve_token_if_needed(position.token, amount_in, position.name, position.symbol):
            return

        deadline = int(time.time()) + SELL_DEADLINE_SECONDS
        path = [position.token, WETH]

        try:
            amounts_out = router_contract.functions.getAmountsOut(amount_in, path).call()
            expected_eth_out = int(amounts_out[-1])
            amount_out_min = int(expected_eth_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception:
            expected_eth_out = 0
            amount_out_min = 0

        nonce = w3.eth.get_transaction_count(wallet_address, "pending")
        tx = router_contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            amount_in,
            amount_out_min,
            path,
            wallet_address,
            deadline,
        ).build_transaction(
            build_tx_params(wallet_address, nonce, GAS_LIMIT_SELL)
        )

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        send(
            "🔴 AUTO SELL SUBMITTED\n\n"
            f"{position.name} ({position.symbol})\n"
            f"Reason: {reason}\n"
            f"Sell percent: {SELL_PERCENT:.0%}\n"
            f"Estimated ETH out: {float(w3.from_wei(expected_eth_out, 'ether')):.6f}\n"
            f"Wallet: {wallet_address}\n"
            f"Tx\n{tx_hex}"
        )

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status == 1:
            send(
                "✅ AUTO SELL COMPLETED\n\n"
                f"{position.name} ({position.symbol})\n"
                f"Reason: {reason}\n"
                f"Sell percent: {SELL_PERCENT:.0%}\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )
        else:
            send(
                "❌ AUTO SELL FAILED\n\n"
                f"{position.name} ({position.symbol})\n"
                f"Reason: {reason}\n"
                f"Wallet: {wallet_address}\n"
                f"Tx\n{tx_hex}"
            )

    except Exception as e:
        send(
            "❌ AUTO SELL ERROR\n\n"
            f"{position.name} ({position.symbol})\n"
            f"Reason: {reason}\n"
            f"Error: {e}"
        )
    finally:
        with POSITION_LOCK:
            ACTIVE_POSITIONS.pop(position.token, None)


# ---------------------------
# POSITION MONITOR
# ---------------------------
def monitor_position(position: Position) -> None:
    send(
        "🛡️ POSITION MONITOR STARTED\n\n"
        f"{position.name} ({position.symbol})\n"
        f"Entry liquidity: {position.entry_liquidity:.4f} ETH\n"
        f"Entry velocity: {position.entry_velocity:.2f}/min\n"
        f"Buy ETH at signal: {position.entry_buy_eth:.4f}\n"
        f"Sell ETH at signal: {position.entry_sell_eth:.4f}\n"
        f"Trailing stop: {format_bool(ENABLE_TRAILING_STOP)}"
    )

    pair_contract = get_pair_contract(position.pair)
    token0 = pair_contract.functions.token0().call()
    token1 = pair_contract.functions.token1().call()

    recent_events = deque()

    while not position.closed:
        try:
            current_liquidity = check_liquidity_eth(position.pair)

            current_block = w3.eth.block_number
            if current_block >= position.last_block:
                try:
                    events = pair_contract.events.Swap.get_logs(
                        from_block=position.last_block,
                        to_block=current_block,
                    )
                    position.last_block = current_block + 1
                except Exception:
                    events = []

                for event in events:
                    side, eth_amount, wallet = parse_swap_direction(event["args"], token0, token1)
                    recent_events.append((now_ts(), side, eth_amount, str(wallet) if wallet else ""))

            cutoff = now_ts() - NEG_FLOW_WINDOW_SECONDS
            while recent_events and recent_events[0][0] < cutoff:
                recent_events.popleft()

            buy_eth_window = 0.0
            sell_eth_window = 0.0
            window_unique_buyers = set()

            for ts, side, eth_amount, wallet in recent_events:
                if side == "buy":
                    buy_eth_window += eth_amount
                    if wallet:
                        window_unique_buyers.add(wallet)
                elif side == "sell":
                    sell_eth_window += eth_amount

            elapsed_window_minutes = max(NEG_FLOW_WINDOW_SECONDS / 60.0, 0.01)
            current_velocity = len(window_unique_buyers) / elapsed_window_minutes

            wallet_balance_raw = int(get_token_contract(position.token).functions.balanceOf(ACCOUNT.address).call())
            if wallet_balance_raw <= 0:
                send(
                    "ℹ️ POSITION CLOSED\n\n"
                    f"{position.name} ({position.symbol})\n"
                    "Wallet balance is zero, stopping monitor."
                )
                break

            estimated_eth_now = estimate_eth_out_for_tokens(position.token, int(wallet_balance_raw * SELL_PERCENT))
            if estimated_eth_now > position.peak_estimated_eth:
                position.peak_estimated_eth = estimated_eth_now

            pnl_pct = 0.0
            if position.purchased_eth > 0:
                pnl_pct = (estimated_eth_now / position.purchased_eth) - 1.0

            if ENABLE_TRAILING_STOP and not position.trailing_active and pnl_pct >= TRAILING_ACTIVATE_PCT:
                position.trailing_active = True
                send(
                    "📈 TRAILING STOP ACTIVATED\n\n"
                    f"{position.name} ({position.symbol})\n"
                    f"Estimated PnL: {pnl_pct * 100:.2f}%\n"
                    f"Peak est ETH: {position.peak_estimated_eth:.6f}"
                )

            if position.trailing_active and position.peak_estimated_eth > 0:
                trail_floor = position.peak_estimated_eth * (1.0 - TRAILING_DISTANCE_PCT)
                if estimated_eth_now < trail_floor:
                    execute_sell(
                        position,
                        f"trailing stop hit | est_eth={estimated_eth_now:.6f} peak={position.peak_estimated_eth:.6f}",
                    )
                    break

            liquidity_floor = position.entry_liquidity * (1.0 - LIQUIDITY_EXIT_DROP_PCT)
            if current_liquidity > 0 and current_liquidity < liquidity_floor:
                execute_sell(
                    position,
                    f"liquidity dropped below floor | current={current_liquidity:.4f} entry={position.entry_liquidity:.4f}",
                )
                break

            emergency_floor = position.entry_liquidity * (1.0 - EMERGENCY_LIQUIDITY_DROP_PCT)
            if current_liquidity > 0 and current_liquidity < emergency_floor:
                execute_sell(
                    position,
                    f"emergency liquidity drain | current={current_liquidity:.4f} entry={position.entry_liquidity:.4f}",
                )
                break

            if sell_eth_window > buy_eth_window and sell_eth_window > 0:
                position.fail_checks_sell_pressure += 1
            else:
                position.fail_checks_sell_pressure = 0

            if position.fail_checks_sell_pressure >= SELL_PRESSURE_REQUIRED_CHECKS:
                execute_sell(
                    position,
                    f"net flow negative | buy_eth={buy_eth_window:.4f} sell_eth={sell_eth_window:.4f}",
                )
                break

            velocity_threshold = max(position.entry_velocity * VELOCITY_EXIT_RATIO, VELOCITY_MIN_ABS)
            if current_velocity < velocity_threshold:
                position.fail_checks_velocity += 1
            else:
                position.fail_checks_velocity = 0

            if position.fail_checks_velocity >= MOMENTUM_FAIL_REQUIRED_CHECKS and sell_eth_window >= buy_eth_window:
                execute_sell(
                    position,
                    f"buyer velocity collapsed | current={current_velocity:.2f}/min threshold={velocity_threshold:.2f}/min",
                )
                break

            time.sleep(POSITION_CHECK_SECONDS)

        except Exception as e:
            print(f"Position monitor error for {position.symbol}: {e}")
            time.sleep(POSITION_CHECK_SECONDS)

    with POSITION_LOCK:
        ACTIVE_POSITIONS.pop(position.token, None)


# ---------------------------
# CORE TRACKER
# ---------------------------
def process_new_token(token: str, pair: str) -> None:
    if pair in ACTIVE_PAIRS:
        return

    ACTIVE_PAIRS.add(pair)

    def worker():
        try:
            pair_contract = get_pair_contract(pair)
            token0 = pair_contract.functions.token0().call()
            token1 = pair_contract.functions.token1().call()

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

            candidate = {
                "token": token,
                "pair": pair,
                "name": name,
                "symbol": symbol,
                "liquidity": liquidity,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "buy_eth": buy_eth,
                "sell_eth": sell_eth,
                "unique_buyer_count": unique_buyer_count,
                "unique_seller_count": len(unique_sellers),
                "buyer_velocity": buyer_velocity,
                "top_buyer_share": top_buyer_share,
                "risk_reason": risk_reason,
            }

            if ONLY_ALERT_BEST_POOL_PER_TOKEN:
                with TOKEN_SELECTION_LOCK:
                    TOKEN_CANDIDATES[token][pair] = candidate
                    if token not in TOKEN_SELECTION_STARTED and token not in TOKEN_DECIDED:
                        TOKEN_SELECTION_STARTED.add(token)
                        threading.Thread(target=finalize_token_selection, args=(token,), daemon=True).start()
            else:
                candidate["pool_score"] = compute_pool_score(candidate)
                if candidate["pool_score"] < POOL_SELECTION_MIN_SCORE:
                    return

                send(build_signal_message(candidate, token))
                send_raw_value(token)

                if purchases_enabled():
                    execute_purchase(
                        token=token,
                        pair=pair,
                        name=name,
                        symbol=symbol,
                        entry_liquidity=liquidity,
                        entry_velocity=buyer_velocity,
                        entry_buy_eth=buy_eth,
                        entry_sell_eth=sell_eth,
                    )

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
    send(
        "ETH early-run scanner started\n"
        f"Run purchase: {format_bool(purchases_enabled())}\n"
        f"Purchase USD: ${PURCHASE_AMOUNT_USD:.2f}\n"
        f"Position monitor: {format_bool(ENABLE_POSITION_MONITOR)}\n"
        f"Best-pool mode: {format_bool(ONLY_ALERT_BEST_POOL_PER_TOKEN)}\n"
        f"Pool selection window: {POOL_SELECTION_WINDOW_SECONDS}s\n"
        f"Liquidity exit drop: {LIQUIDITY_EXIT_DROP_PCT:.0%}\n"
        f"Trailing stop: {format_bool(ENABLE_TRAILING_STOP)}\n"
        "Alert mode: ONLY coins that fully pass buy criteria"
    )

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
