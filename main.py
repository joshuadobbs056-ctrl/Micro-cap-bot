import os
import sys
import time
import threading
import subprocess
from typing import Optional, Dict, Any, Tuple, List
from collections import deque


def install(package: str):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


try:
    import requests
except Exception:
    install("requests")
    import requests

try:
    from web3 import Web3
except Exception:
    install("web3")
    from web3 import Web3


# -------------------------
# SHARED HTTP SESSION
# -------------------------
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


# -------------------------
# ENV
# -------------------------
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

# supports both old and new variable names
RUN_AUTO_BUY = os.getenv("RUN_AUTO_BUY", os.getenv("RUN_PURCHASE", "off")).strip().lower()  # on/off

START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
BUY_SIZE_USD = float(os.getenv("BUY_SIZE_USD", os.getenv("PURCHASE_AMOUNT_USD", "25")))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "30"))
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "1800"))
LIVE_ACCOUNT_UPDATE_SECONDS = int(os.getenv("LIVE_ACCOUNT_UPDATE_SECONDS", "300"))
POSITION_CHECK_SECONDS = int(os.getenv("POSITION_CHECK_SECONDS", "30"))

TOP_COINS_LIMIT = int(os.getenv("TOP_COINS_LIMIT", "100"))
MIN_24H_VOLUME_USD = float(os.getenv("MIN_24H_VOLUME_USD", "5000000"))
MIN_MARKET_CAP_USD = float(os.getenv("MIN_MARKET_CAP_USD", "50000000"))
LOOKBACK_POINTS = int(os.getenv("LOOKBACK_POINTS", "6"))  # with 30s check, 6 = 3 minutes
ENTRY_PUMP_PCT = float(os.getenv("ENTRY_PUMP_PCT", "2.5"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "5"))

TRAIL_ARM_PCT = float(os.getenv("TRAIL_ARM_PCT", "5"))
TRAIL_DROP_PCT = float(os.getenv("TRAIL_DROP_PCT", "2"))

SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "300"))
GAS_LIMIT_BUY = int(os.getenv("GAS_LIMIT_BUY", "450000"))
GAS_LIMIT_APPROVE = int(os.getenv("GAS_LIMIT_APPROVE", "120000"))
GAS_LIMIT_SELL = int(os.getenv("GAS_LIMIT_SELL", "450000"))
GAS_LIMIT_BUY_V3 = int(os.getenv("GAS_LIMIT_BUY_V3", "550000"))
GAS_LIMIT_SELL_V3 = int(os.getenv("GAS_LIMIT_SELL_V3", "550000"))

BUY_DEADLINE_SECONDS = int(os.getenv("BUY_DEADLINE_SECONDS", "180"))
SELL_DEADLINE_SECONDS = int(os.getenv("SELL_DEADLINE_SECONDS", "180"))

MIN_ETH_GAS_RESERVE = float(os.getenv("MIN_ETH_GAS_RESERVE", "0.01"))
FAILED_BUY_COOLDOWN_SECONDS = int(os.getenv("FAILED_BUY_COOLDOWN_SECONDS", "900"))
MAX_FAILED_SELL_ATTEMPTS = int(os.getenv("MAX_FAILED_SELL_ATTEMPTS", "2"))

TELEGRAM_COOLDOWN_SECONDS = int(os.getenv("TELEGRAM_COOLDOWN_SECONDS", "3"))
PORTFOLIO_UPDATE_SECONDS = int(os.getenv("PORTFOLIO_UPDATE_SECONDS", "300"))

V3_DEFAULT_FEE = int(os.getenv("V3_DEFAULT_FEE", "3000"))
V3_FEE_CANDIDATES = [500, 3000, 10000]

# exclude wrapped and stables by default
EXCLUDED_SYMBOLS = {
    s.strip().upper()
    for s in os.getenv(
        "EXCLUDED_SYMBOLS",
        "WETH,WBTC,USDT,USDC,DAI,FDUSD,TUSD,PYUSD,USDE,LUSD,USDD,USDB"
    ).split(",")
    if s.strip()
}


# -------------------------
# WEB3
# -------------------------
if not NODE:
    raise RuntimeError("NODE missing")

if NODE.startswith("wss://"):
    NODE = NODE.replace("wss://", "https://", 1)
    print("Converted WSS node to HTTPS.")
elif NODE.startswith("ws://"):
    NODE = NODE.replace("ws://", "http://", 1)
    print("Converted WS node to HTTP.")

w3 = Web3(Web3.HTTPProvider(NODE, request_kwargs={"timeout": 30}))

if not w3.is_connected():
    raise RuntimeError("Node connection failed")

print("Connected to node")

ACCOUNT = None
if PRIVATE_KEY:
    try:
        ACCOUNT = w3.eth.account.from_key(PRIVATE_KEY)
        print(f"Loaded wallet: {ACCOUNT.address}")
    except Exception as e:
        raise RuntimeError(f"Bad PRIVATE_KEY: {e}")


# -------------------------
# CONSTANTS / ABIS
# -------------------------
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
ROUTER = Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
V3_ROUTER = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")
V3_QUOTER = Web3.to_checksum_address("0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6")
CHAINLINK_ETH_USD = Web3.to_checksum_address("0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419")

ERC20_ABI = [
    {
        "name": "name",
        "outputs": [{"type": "string"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "symbol",
        "outputs": [{"type": "string"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "balanceOf",
        "outputs": [{"type": "uint256"}],
        "inputs": [{"name": "owner", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "allowance",
        "outputs": [{"type": "uint256"}],
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "approve",
        "outputs": [{"type": "bool"}],
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "outputs": [{"name": "", "type": "uint256[]"}],
        "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
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

V3_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

V3_QUOTER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenIn", "type": "address"},
            {"internalType": "address", "name": "tokenOut", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
        ],
        "name": "quoteExactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
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

router = w3.eth.contract(address=ROUTER, abi=ROUTER_ABI)
v3_router = w3.eth.contract(address=V3_ROUTER, abi=V3_ROUTER_ABI)
v3_quoter = w3.eth.contract(address=V3_QUOTER, abi=V3_QUOTER_ABI)
eth_usd_feed = w3.eth.contract(address=CHAINLINK_ETH_USD, abi=CHAINLINK_ETH_USD_ABI)


# -------------------------
# GLOBAL STATE
# -------------------------
LOCK = threading.Lock()
LAST_TELEGRAM_SEND_TS = 0.0

FAILED_LIVE_BUYS: Dict[str, float] = {}
FAILED_SELL_ATTEMPTS: Dict[str, int] = {}

PRICE_HISTORY: Dict[str, deque] = {}
TOP_MARKET_CACHE: Dict[str, dict] = {}
CONTRACT_CACHE: Dict[str, Optional[str]] = {}
LAST_SIGNAL_TS: Dict[str, float] = {}

PAPER_POSITIONS: Dict[str, dict] = {}
LIVE_POSITIONS: Dict[str, dict] = {}
ACCOUNT_CASH = START_BALANCE


# -------------------------
# TELEGRAM
# -------------------------
def send(msg: str):
    global LAST_TELEGRAM_SEND_TS

    MAX_TELEGRAM_LEN = 3900
    text = str(msg)

    if len(text) > MAX_TELEGRAM_LEN:
        text = text[:MAX_TELEGRAM_LEN] + "\n\n...[truncated]"

    now = time.time()
    if now - LAST_TELEGRAM_SEND_TS < TELEGRAM_COOLDOWN_SECONDS:
        print("Telegram throttled (local cooldown)")
        print(text)
        return

    LAST_TELEGRAM_SEND_TS = now

    delivered = False
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            r = SESSION.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id": CHAT_ID,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if r.status_code == 200:
                delivered = True
            else:
                print(f"Telegram send failed: status={r.status_code} body={r.text}")
        except Exception as e:
            print(f"Telegram send exception: {e}")
    else:
        print("Telegram not configured: TELEGRAM_TOKEN or CHAT_ID missing")

    if delivered:
        print("Telegram delivered")
    print(text)


# -------------------------
# HELPERS
# -------------------------
def now_ts() -> float:
    return time.time()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def safe_block_number(default: int = 0) -> int:
    try:
        return int(w3.eth.block_number)
    except Exception as e:
        print("safe_block_number error:", e)
        return default


def get_token_contract(token: str):
    return w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)


def get_token_meta(token: str):
    try:
        c = get_token_contract(token)
        name = c.functions.name().call()
        symbol = c.functions.symbol().call()
        decimals = int(c.functions.decimals().call())
        return str(name), str(symbol), decimals
    except Exception:
        return "Unknown", "UNK", 18


def get_eth_usd_price() -> float:
    try:
        data = eth_usd_feed.functions.latestRoundData().call()
        return int(data[1]) / 10**8
    except Exception as e:
        print("latestRoundData error:", e)
        raise


def usd_to_eth(usd_amount: float) -> float:
    eth_usd = get_eth_usd_price()
    if eth_usd <= 0:
        raise RuntimeError("ETH/USD price unavailable")
    return usd_amount / eth_usd


def wei_to_eth(value_wei: int) -> float:
    return float(w3.from_wei(int(value_wei), "ether"))


def build_tx_params(wallet_address: str, nonce: int, gas: int, value: int = 0) -> dict:
    tx = {
        "from": wallet_address,
        "value": value,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
    }

    try:
        latest_block = w3.eth.get_block("latest")
        base_fee = latest_block.get("baseFeePerGas")
    except Exception:
        base_fee = None

    if base_fee is not None:
        try:
            priority_fee = int(w3.to_wei(2, "gwei"))
        except Exception:
            priority_fee = 2_000_000_000

        max_fee = int(base_fee * 2 + priority_fee)
        tx["maxPriorityFeePerGas"] = priority_fee
        tx["maxFeePerGas"] = max_fee
    else:
        tx["gasPrice"] = int(w3.eth.gas_price)

    if gas and gas > 0:
        tx["gas"] = int(gas)

    return tx


def estimate_total_buy_cost_wei(value_wei: int, gas_limit: int = None) -> int:
    if gas_limit is None:
        gas_limit = GAS_LIMIT_BUY
    try:
        gas_price = int(w3.eth.gas_price)
    except Exception:
        gas_price = 0
    gas_buffer_wei = gas_price * int(gas_limit)
    reserve_wei = int(w3.to_wei(MIN_ETH_GAS_RESERVE, "ether"))
    return int(value_wei + gas_buffer_wei + reserve_wei)


def record_failed_sell(token: str):
    with LOCK:
        FAILED_SELL_ATTEMPTS[token] = FAILED_SELL_ATTEMPTS.get(token, 0) + 1


def clear_failed_sell(token: str):
    with LOCK:
        FAILED_SELL_ATTEMPTS.pop(token, None)


def sell_attempts_exceeded(token: str) -> bool:
    with LOCK:
        return FAILED_SELL_ATTEMPTS.get(token, 0) >= MAX_FAILED_SELL_ATTEMPTS


# -------------------------
# COINGECKO
# -------------------------
def coingecko_get(path: str, params: Optional[dict] = None) -> Optional[Any]:
    try:
        r = SESSION.get(f"https://api.coingecko.com/api/v3{path}", params=params or {}, timeout=20)
        if r.status_code != 200:
            print(f"CoinGecko error {r.status_code}: {path} | {r.text[:200]}")
            return None
        body = (r.text or "").strip()
        if not body:
            return None
        return r.json()
    except Exception as e:
        print(f"CoinGecko request failed for {path}: {e}")
        return None


def fetch_top_markets() -> List[dict]:
    data = coingecko_get(
        "/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": min(max(TOP_COINS_LIMIT, 1), 250),
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "1h,24h",
        },
    )

    if not isinstance(data, list):
        return []

    filtered = []
    for coin in data:
        symbol = str(coin.get("symbol") or "").upper()
        market_cap = safe_float(coin.get("market_cap"))
        vol = safe_float(coin.get("total_volume"))
        price = safe_float(coin.get("current_price"))

        if not symbol:
            continue
        if symbol in EXCLUDED_SYMBOLS:
            continue
        if market_cap < MIN_MARKET_CAP_USD:
            continue
        if vol < MIN_24H_VOLUME_USD:
            continue
        if price <= 0:
            continue

        filtered.append(coin)

    return filtered[:TOP_COINS_LIMIT]


def get_ethereum_contract_for_coin(coin_id: str) -> Optional[str]:
    with LOCK:
        if coin_id in CONTRACT_CACHE:
            return CONTRACT_CACHE[coin_id]

    data = coingecko_get("/coins/" + coin_id, params={"localization": "false", "tickers": "false", "market_data": "false", "community_data": "false", "developer_data": "false", "sparkline": "false"})
    contract = None

    if isinstance(data, dict):
        platforms = data.get("platforms") or {}
        eth_address = platforms.get("ethereum")
        if eth_address:
            try:
                contract = Web3.to_checksum_address(eth_address)
            except Exception:
                contract = None

    with LOCK:
        CONTRACT_CACHE[coin_id] = contract
    return contract


# -------------------------
# QUOTES / ROUTING
# -------------------------
def get_v2_quote(amount_in_wei: int, token: str) -> Tuple[bool, int, int, str]:
    path = [WETH, Web3.to_checksum_address(token)]
    try:
        amounts_out = router.functions.getAmountsOut(amount_in_wei, path).call()
        expected_out = int(amounts_out[-1])
        if expected_out <= 0:
            return False, 0, 0, "quote returned zero"
        amount_out_min = int(expected_out * (10000 - SLIPPAGE_BPS) / 10000)
        if amount_out_min <= 0:
            return False, 0, 0, "amountOutMin <= 0"
        return True, expected_out, amount_out_min, "ok"
    except Exception as e:
        return False, 0, 0, f"no usable V2 route: {e}"


def get_v2_reverse_quote(amount_in_raw: int, token: str) -> Tuple[bool, int, str]:
    path = [Web3.to_checksum_address(token), WETH]
    try:
        amounts_out = router.functions.getAmountsOut(int(amount_in_raw), path).call()
        expected_out = int(amounts_out[-1])
        if expected_out <= 0:
            return False, 0, "reverse quote returned zero"
        return True, expected_out, "ok"
    except Exception as e:
        return False, 0, f"no usable V2 reverse route: {e}"


def get_v3_quote(amount_in_wei: int, token: str, fee: int) -> Tuple[bool, int, int, str]:
    try:
        expected_out = int(
            v3_quoter.functions.quoteExactInputSingle(
                WETH,
                Web3.to_checksum_address(token),
                int(fee),
                int(amount_in_wei),
                0,
            ).call()
        )
        if expected_out <= 0:
            return False, 0, 0, "V3 quote returned zero"
        amount_out_min = int(expected_out * (10000 - SLIPPAGE_BPS) / 10000)
        if amount_out_min <= 0:
            return False, 0, 0, "V3 amountOutMin <= 0"
        return True, expected_out, amount_out_min, "ok"
    except Exception as e:
        return False, 0, 0, f"no usable V3 route: {e}"


def get_v3_reverse_quote(amount_in_raw: int, token: str, fee: int) -> Tuple[bool, int, str]:
    try:
        expected_out = int(
            v3_quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(token),
                WETH,
                int(fee),
                int(amount_in_raw),
                0,
            ).call()
        )
        if expected_out <= 0:
            return False, 0, "V3 reverse quote returned zero"
        return True, expected_out, "ok"
    except Exception as e:
        return False, 0, f"no usable V3 reverse route: {e}"


def get_best_buy_route(amount_in_wei: int, token: str) -> Tuple[bool, str, int, int, int, str]:
    token = Web3.to_checksum_address(token)
    best = None

    ok, expected_out, amount_out_min, reason = get_v2_quote(amount_in_wei, token)
    if ok:
        best = ("V2", 0, expected_out, amount_out_min, "ok")

    for fee in V3_FEE_CANDIDATES:
        ok3, expected_out3, amount_out_min3, reason3 = get_v3_quote(amount_in_wei, token, fee)
        if ok3:
            if best is None or expected_out3 > best[2]:
                best = ("V3", fee, expected_out3, amount_out_min3, "ok")

    if best:
        return True, best[0], best[1], best[2], best[3], best[4]

    return False, "", 0, 0, 0, "no route found"


# -------------------------
# LIVE BUY / SELL
# -------------------------
def approve_token_if_needed(token: str, amount_raw: int, spender: str) -> bool:
    if not ACCOUNT:
        return False

    token_contract = get_token_contract(token)
    wallet = ACCOUNT.address
    spender = Web3.to_checksum_address(spender)

    try:
        allowance = int(token_contract.functions.allowance(wallet, spender).call())
        if allowance >= amount_raw:
            return True

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        tx = token_contract.functions.approve(
            spender,
            2**256 - 1,
        ).build_transaction(build_tx_params(wallet, nonce, GAS_LIMIT_APPROVE))

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status == 1:
            send(f"✅ APPROVE OK\n{token}\nSpender {spender}\nTx {tx_hash.hex()}")
            return True

        send(f"❌ APPROVE FAILED\n{token}\nSpender {spender}\nTx {tx_hash.hex()}")
        return False
    except Exception as e:
        send(f"❌ APPROVE ERROR\n{token}\nSpender {spender}\n{e}")
        return False


def execute_live_buy(token: str, coin_id: str, symbol: str, entry_price_usd: float) -> Optional[dict]:
    if not ACCOUNT:
        send("⚠️ LIVE BUY SKIPPED\nReason: PRIVATE_KEY not loaded")
        return None

    wallet = ACCOUNT.address
    token = Web3.to_checksum_address(token)

    with LOCK:
        last_fail = FAILED_LIVE_BUYS.get(token, 0.0)
    if now_ts() - last_fail < FAILED_BUY_COOLDOWN_SECONDS:
        remaining = int(FAILED_BUY_COOLDOWN_SECONDS - (now_ts() - last_fail))
        send(
            f"⚠️ LIVE BUY SKIPPED\n\n"
            f"{symbol}\n"
            f"Token\n{token}\n\n"
            f"Reason: failed buy cooldown active\n"
            f"Retry In {remaining}s"
        )
        return None

    try:
        _, _, decimals = get_token_meta(token)

        eth_amount = usd_to_eth(BUY_SIZE_USD)
        value_wei = int(w3.to_wei(eth_amount, "ether"))

        wallet_balance = int(w3.eth.get_balance(wallet))
        total_required_wei = estimate_total_buy_cost_wei(value_wei, GAS_LIMIT_BUY_V3)

        if wallet_balance < total_required_wei:
            send(
                f"⚠️ LIVE BUY SKIPPED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Reason: insufficient ETH for buy + gas reserve\n"
                f"Wallet ETH {wei_to_eth(wallet_balance):.6f}\n"
                f"Needed ETH {wei_to_eth(total_required_wei):.6f}"
            )
            return None

        route_ok, route_type, fee_used, expected_out, amount_out_min, route_reason = get_best_buy_route(value_wei, token)
        if not route_ok:
            with LOCK:
                FAILED_LIVE_BUYS[token] = now_ts()
            send(
                f"⚠️ LIVE BUY SKIPPED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Reason: {route_reason}"
            )
            return None

        token_contract = get_token_contract(token)
        balance_before = int(token_contract.functions.balanceOf(wallet).call())

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        deadline = int(time.time()) + BUY_DEADLINE_SECONDS

        if route_type == "V2":
            tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                amount_out_min,
                [WETH, token],
                wallet,
                deadline,
            ).build_transaction(build_tx_params(wallet, nonce, GAS_LIMIT_BUY, value=value_wei))
        else:
            params = (
                WETH,
                token,
                int(fee_used),
                wallet,
                deadline,
                int(value_wei),
                int(amount_out_min),
                0,
            )

            gas_limit = GAS_LIMIT_BUY_V3
            try:
                gas_estimate = v3_router.functions.exactInputSingle(params).estimate_gas(
                    build_tx_params(wallet, nonce, 0, value=value_wei)
                )
                gas_limit = max(int(gas_estimate * 1.25), GAS_LIMIT_BUY_V3)
            except Exception as e:
                print(f"V3 gas estimate failed for {symbol}: {e}")

            tx = v3_router.functions.exactInputSingle(params).build_transaction(
                build_tx_params(wallet, nonce, gas_limit, value=value_wei)
            )

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status != 1:
            with LOCK:
                FAILED_LIVE_BUYS[token] = now_ts()
            send(
                f"❌ LIVE BUY FAILED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Route {route_type}{f' fee={fee_used}' if route_type == 'V3' else ''}\n"
                f"Tx {tx_hash.hex()}\n"
                f"Reason: transaction reverted"
            )
            return None

        balance_after = int(token_contract.functions.balanceOf(wallet).call())
        token_amount_raw = max(balance_after - balance_before, 0)

        if token_amount_raw <= 0:
            with LOCK:
                FAILED_LIVE_BUYS[token] = now_ts()
            send(
                f"❌ LIVE BUY FAILED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Tx {tx_hash.hex()}\n"
                f"Reason: no token balance received"
            )
            return None

        token_amount = token_amount_raw / (10 ** decimals)

        send(
            f"🟢 LIVE BUY OPENED\n\n"
            f"{symbol}\n"
            f"Coin ID {coin_id}\n"
            f"Token\n{token}\n\n"
            f"Route {route_type}{f' fee={fee_used}' if route_type == 'V3' else ''}\n"
            f"Entry Price ${entry_price_usd:.8f}\n"
            f"Buy Size ${BUY_SIZE_USD:.2f}\n"
            f"Approx ETH {eth_amount:.6f}\n"
            f"Quoted Tokens {expected_out / (10 ** decimals):,.6f}\n"
            f"Received Tokens {token_amount:,.6f}\n"
            f"Tx {tx_hash.hex()}"
        )

        return {
            "coin_id": coin_id,
            "token": token,
            "symbol": symbol,
            "entry_price": entry_price_usd,
            "token_amount_raw": token_amount_raw,
            "decimals": decimals,
            "opened": now_ts(),
            "peak_price": entry_price_usd if entry_price_usd > 0 else 0.0,
            "entry_value_usd": BUY_SIZE_USD,
            "current_price_usd": entry_price_usd if entry_price_usd > 0 else 0.0,
            "route_type": route_type,
            "fee_used": fee_used,
        }

    except Exception as e:
        with LOCK:
            FAILED_LIVE_BUYS[token] = now_ts()
        send(
            f"❌ LIVE BUY ERROR\n\n"
            f"{symbol}\n"
            f"Token\n{token}\n\n"
            f"{e}"
        )
        return None


def execute_live_sell(position: dict, current_price_usd: float, reason: str) -> bool:
    if not ACCOUNT:
        return False

    token = position["token"]
    symbol = position["symbol"]
    amount_raw = position["token_amount_raw"]
    wallet = ACCOUNT.address
    route_type = position.get("route_type", "V2")
    fee = int(position.get("fee_used", 0))
    m = get_live_position_metrics(position)

    if sell_attempts_exceeded(token):
        send(
            f"⚠️ SELL BLOCKED\n\n"
            f"{symbol}\n"
            f"Token\n{token}\n\n"
            f"Reason: max failed sell attempts reached\n"
            f"Attempts {MAX_FAILED_SELL_ATTEMPTS}"
        )
        return False

    try:
        if amount_raw <= 0:
            return False

        spender = V3_ROUTER if route_type == "V3" else ROUTER
        if not approve_token_if_needed(token, amount_raw, spender):
            record_failed_sell(token)
            return False

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        deadline = int(time.time()) + SELL_DEADLINE_SECONDS

        if route_type == "V3" and fee > 0:
            try:
                expected_eth_out = int(
                    v3_quoter.functions.quoteExactInputSingle(
                        Web3.to_checksum_address(token),
                        WETH,
                        int(fee),
                        int(amount_raw),
                        0,
                    ).call()
                )
                amount_out_min = int(expected_eth_out * (10000 - SLIPPAGE_BPS) / 10000)
                if amount_out_min <= 0:
                    raise RuntimeError("amountOutMin <= 0")

                params = (
                    Web3.to_checksum_address(token),
                    WETH,
                    int(fee),
                    wallet,
                    deadline,
                    int(amount_raw),
                    int(amount_out_min),
                    0,
                )

                gas_limit = GAS_LIMIT_SELL_V3
                try:
                    gas_estimate = v3_router.functions.exactInputSingle(params).estimate_gas(
                        build_tx_params(wallet, nonce, 0, value=0)
                    )
                    gas_limit = max(int(gas_estimate * 1.25), GAS_LIMIT_SELL_V3)
                except Exception as e:
                    print(f"V3 sell gas estimate failed for {symbol}: {e}")

                tx = v3_router.functions.exactInputSingle(params).build_transaction(
                    build_tx_params(wallet, nonce, gas_limit, value=0)
                )
            except Exception as e:
                print(f"V3 sell path failed for {symbol}, falling back to V2: {e}")
                route_type = "V2"

        if route_type == "V2":
            path = [token, WETH]
            try:
                amounts_out = router.functions.getAmountsOut(amount_raw, path).call()
                expected_eth_out = int(amounts_out[-1])
                amount_out_min = int(expected_eth_out * (10000 - SLIPPAGE_BPS) / 10000)
            except Exception as e:
                record_failed_sell(token)
                send(
                    f"❌ LIVE SELL SKIPPED\n\n"
                    f"{symbol}\n"
                    f"Token\n{token}\n\n"
                    f"Reason: no usable V2 sell route\n{e}"
                )
                return False

            tx = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                amount_raw,
                amount_out_min,
                path,
                wallet,
                deadline,
            ).build_transaction(build_tx_params(wallet, nonce, GAS_LIMIT_SELL))

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status == 1:
            clear_failed_sell(token)
            send(
                f"🔴 LIVE SELL CLOSED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Tokens Held {m['token_amount']:,.6f}\n"
                f"Entry Value ${m['entry_value_usd']:.2f}\n"
                f"Current Value ${m['current_value_usd']:.2f}\n"
                f"PnL ${m['pnl_usd']:.2f}\n"
                f"PnL {m['pnl_pct']:.2f}%\n\n"
                f"Current Price ${current_price_usd:.8f}\n"
                f"Reason: {reason}\n"
                f"Tx {tx_hash.hex()}"
            )
            return True

        record_failed_sell(token)
        send(
            f"❌ LIVE SELL FAILED\n\n"
            f"{symbol}\n"
            f"Token\n{token}\n\n"
            f"PnL ${m['pnl_usd']:.2f}\n"
            f"PnL {m['pnl_pct']:.2f}%\n"
            f"Tx {tx_hash.hex()}"
        )
        return False

    except Exception as e:
        record_failed_sell(token)
        send(f"❌ LIVE SELL ERROR\n{symbol}\n{token}\n{e}")
        return False


# -------------------------
# POSITION METRICS
# -------------------------
def get_live_position_metrics(pos: dict) -> Dict[str, float]:
    decimals = int(pos.get("decimals", 18))
    token_amount_raw = int(pos.get("token_amount_raw", 0))
    token_amount = token_amount_raw / (10 ** decimals) if decimals >= 0 else 0.0

    entry_value_usd = safe_float(pos.get("entry_value_usd"), BUY_SIZE_USD)
    current_price = safe_float(pos.get("current_price_usd"), 0.0)
    peak_price = safe_float(pos.get("peak_price"), 0.0)

    current_value_usd = token_amount * current_price if current_price > 0 else 0.0
    pnl_usd = current_value_usd - entry_value_usd
    pnl_pct = ((pnl_usd / entry_value_usd) * 100.0) if entry_value_usd > 0 else 0.0

    peak_value_usd = token_amount * peak_price if peak_price > 0 else 0.0
    peak_pnl_usd = peak_value_usd - entry_value_usd
    peak_pnl_pct = ((peak_pnl_usd / entry_value_usd) * 100.0) if entry_value_usd > 0 else 0.0

    return {
        "token_amount": token_amount,
        "entry_value_usd": entry_value_usd,
        "current_value_usd": current_value_usd,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "peak_value_usd": peak_value_usd,
        "peak_pnl_usd": peak_pnl_usd,
        "peak_pnl_pct": peak_pnl_pct,
    }


def get_live_account_snapshot() -> Dict[str, float]:
    eth_usd = 0.0
    try:
        eth_usd = get_eth_usd_price()
    except Exception:
        eth_usd = 0.0

    wallet_eth = 0.0
    cash_value_usd = 0.0

    if ACCOUNT:
        try:
            wallet_balance_wei = int(w3.eth.get_balance(ACCOUNT.address))
            wallet_eth = wei_to_eth(wallet_balance_wei)
            if eth_usd > 0:
                cash_value_usd = wallet_eth * eth_usd
        except Exception:
            wallet_eth = 0.0
            cash_value_usd = 0.0

    open_position_value_usd = 0.0
    unrealized_pnl_usd = 0.0
    invested_value_usd = 0.0

    for pos in list(LIVE_POSITIONS.values()):
        m = get_live_position_metrics(pos)
        open_position_value_usd += m["current_value_usd"]
        unrealized_pnl_usd += m["pnl_usd"]
        invested_value_usd += m["entry_value_usd"]

    total_equity_usd = cash_value_usd + open_position_value_usd
    unrealized_pnl_pct = ((unrealized_pnl_usd / invested_value_usd) * 100.0) if invested_value_usd > 0 else 0.0

    return {
        "wallet_eth": wallet_eth,
        "eth_usd": eth_usd,
        "cash_value_usd": cash_value_usd,
        "open_position_value_usd": open_position_value_usd,
        "total_equity_usd": total_equity_usd,
        "unrealized_pnl_usd": unrealized_pnl_usd,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "invested_value_usd": invested_value_usd,
    }


# -------------------------
# PAPER MODE
# -------------------------
def open_paper_position(coin_id: str, token: str, symbol: str, price: float) -> bool:
    global ACCOUNT_CASH

    if len(PAPER_POSITIONS) >= MAX_OPEN_TRADES:
        return False
    if ACCOUNT_CASH < BUY_SIZE_USD:
        return False
    if price <= 0:
        return False

    qty = BUY_SIZE_USD / price
    PAPER_POSITIONS[coin_id] = {
        "coin_id": coin_id,
        "token": token,
        "symbol": symbol,
        "entry_price": price,
        "qty": qty,
        "opened": now_ts(),
        "peak_price": price,
        "current_price_usd": price,
        "entry_value_usd": BUY_SIZE_USD,
    }
    ACCOUNT_CASH -= BUY_SIZE_USD

    send(
        f"🧪 PAPER BUY OPENED\n\n"
        f"{symbol}\n"
        f"Coin ID {coin_id}\n"
        f"Token\n{token}\n\n"
        f"Entry Price ${price:.8f}\n"
        f"Buy Size ${BUY_SIZE_USD:.2f}\n"
        f"Qty {qty:,.6f}\n"
        f"Open Trades {len(PAPER_POSITIONS)}/{MAX_OPEN_TRADES}"
    )
    return True


def monitor_paper_positions():
    global ACCOUNT_CASH
    while True:
        try:
            top = fetch_top_markets()
            market_by_id = {c.get("id"): c for c in top if c.get("id")}

            for coin_id, pos in list(PAPER_POSITIONS.items()):
                market = market_by_id.get(coin_id)
                if not market:
                    continue

                current_price = safe_float(market.get("current_price"))
                if current_price <= 0:
                    continue

                pos["current_price_usd"] = current_price
                if current_price > safe_float(pos.get("peak_price"), 0.0):
                    pos["peak_price"] = current_price

                qty = safe_float(pos.get("qty"))
                entry_price = safe_float(pos.get("entry_price"))
                peak_price = safe_float(pos.get("peak_price"))

                current_value = qty * current_price
                pnl_pct = ((current_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
                peak_pnl_pct = ((peak_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

                if peak_pnl_pct >= TRAIL_ARM_PCT:
                    trail_floor_price = peak_price * (1.0 - TRAIL_DROP_PCT / 100.0)
                    if current_price <= trail_floor_price:
                        ACCOUNT_CASH += current_value
                        send(
                            f"💰 PAPER TRAILING EXIT\n\n"
                            f"{pos['symbol']}\n"
                            f"Entry ${entry_price:.8f}\n"
                            f"Exit ${current_price:.8f}\n\n"
                            f"Peak PnL {peak_pnl_pct:.2f}%\n"
                            f"Final PnL {pnl_pct:.2f}%\n"
                            f"Final Value ${current_value:.2f}\n"
                            f"Reason: trailed {TRAIL_DROP_PCT:.2f}% from peak after arming at {TRAIL_ARM_PCT:.2f}%"
                        )
                        PAPER_POSITIONS.pop(coin_id, None)
                        continue

                if pnl_pct <= -abs(STOP_LOSS_PCT):
                    ACCOUNT_CASH += current_value
                    send(
                        f"🛑 PAPER STOP LOSS EXIT\n\n"
                        f"{pos['symbol']}\n"
                        f"Entry ${entry_price:.8f}\n"
                        f"Exit ${current_price:.8f}\n"
                        f"PnL {pnl_pct:.2f}%\n"
                        f"Final Value ${current_value:.2f}"
                    )
                    PAPER_POSITIONS.pop(coin_id, None)

        except Exception as e:
            print(f"monitor_paper_positions error: {e}")

        time.sleep(POSITION_CHECK_SECONDS)


# -------------------------
# LIVE POSITION MONITOR
# -------------------------
def monitor_live_positions():
    while True:
        try:
            top = fetch_top_markets()
            market_by_id = {c.get("id"): c for c in top if c.get("id")}

            for token, pos in list(LIVE_POSITIONS.items()):
                coin_id = pos.get("coin_id")
                market = market_by_id.get(coin_id)
                if not market:
                    continue

                current_price = safe_float(market.get("current_price"))
                if current_price <= 0:
                    continue

                entry_price = safe_float(pos.get("entry_price"))
                pos["current_price_usd"] = current_price

                if current_price > safe_float(pos.get("peak_price"), 0.0):
                    pos["peak_price"] = current_price

                peak_price = safe_float(pos.get("peak_price"))
                m = get_live_position_metrics(pos)

                peak_pnl = ((peak_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
                pnl_pct = ((current_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

                if peak_pnl >= TRAIL_ARM_PCT:
                    trail_floor_price = peak_price * (1.0 - TRAIL_DROP_PCT / 100.0)
                    if current_price <= trail_floor_price:
                        sold = execute_live_sell(
                            pos,
                            current_price,
                            f"trailed {TRAIL_DROP_PCT:.2f}% from peak after arming at {TRAIL_ARM_PCT:.2f}%"
                        )
                        if sold:
                            LIVE_POSITIONS.pop(token, None)
                            continue

                if pnl_pct <= -abs(STOP_LOSS_PCT):
                    sold = execute_live_sell(pos, current_price, f"stop loss {STOP_LOSS_PCT:.2f}%")
                    if sold:
                        LIVE_POSITIONS.pop(token, None)

        except Exception as e:
            print(f"monitor_live_positions error: {e}")

        time.sleep(POSITION_CHECK_SECONDS)


# -------------------------
# SIGNAL ENGINE
# -------------------------
def update_price_history(coin: dict):
    coin_id = str(coin.get("id") or "")
    if not coin_id:
        return

    price = safe_float(coin.get("current_price"))
    if price <= 0:
        return

    with LOCK:
        if coin_id not in PRICE_HISTORY:
            PRICE_HISTORY[coin_id] = deque(maxlen=max(LOOKBACK_POINTS, 2))
        PRICE_HISTORY[coin_id].append((now_ts(), price))
        TOP_MARKET_CACHE[coin_id] = coin


def get_signal_for_coin(coin: dict) -> Tuple[bool, str]:
    coin_id = str(coin.get("id") or "")
    symbol = str(coin.get("symbol") or "").upper()
    price = safe_float(coin.get("current_price"))

    if not coin_id or price <= 0:
        return False, "bad market data"

    with LOCK:
        hist = list(PRICE_HISTORY.get(coin_id, []))
        last_signal = LAST_SIGNAL_TS.get(coin_id, 0.0)

    if len(hist) < max(LOOKBACK_POINTS, 2):
        return False, "not enough history"

    if now_ts() - last_signal < max(CHECK_INTERVAL_SECONDS * LOOKBACK_POINTS, 180):
        return False, "signal cooldown active"

    old_price = safe_float(hist[0][1])
    if old_price <= 0:
        return False, "old price invalid"

    move_pct = ((price - old_price) / old_price) * 100.0
    if move_pct < ENTRY_PUMP_PCT:
        return False, f"momentum too low: {move_pct:.2f}% < {ENTRY_PUMP_PCT:.2f}%"

    if symbol in EXCLUDED_SYMBOLS:
        return False, f"excluded symbol {symbol}"

    return True, f"momentum breakout {move_pct:.2f}% over {len(hist)} points"


def process_signal(coin: dict):
    coin_id = str(coin.get("id") or "")
    symbol = str(coin.get("symbol") or "").upper()
    price = safe_float(coin.get("current_price"))

    if not coin_id or not symbol or price <= 0:
        return

    signal_ok, signal_reason = get_signal_for_coin(coin)
    if not signal_ok:
        return

    contract = get_ethereum_contract_for_coin(coin_id)
    if not contract:
        print(f"{symbol}: no ethereum contract found")
        return

    with LOCK:
        LAST_SIGNAL_TS[coin_id] = now_ts()

    if RUN_AUTO_BUY == "on":
        if len(LIVE_POSITIONS) >= MAX_OPEN_TRADES:
            return
        if contract in LIVE_POSITIONS:
            return

        pos = execute_live_buy(contract, coin_id, symbol, price)
        if pos:
            LIVE_POSITIONS[contract] = pos
    else:
        if coin_id not in PAPER_POSITIONS:
            open_paper_position(coin_id, contract, symbol, price)


def scanner_loop():
    send("Top 100 momentum scanner ON")

    while True:
        try:
            top = fetch_top_markets()
            if not top:
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            for coin in top:
                update_price_history(coin)

            for coin in top:
                process_signal(coin)

        except Exception as e:
            print(f"scanner_loop error: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)


# -------------------------
# PORTFOLIO
# -------------------------
def portfolio_loop():
    while True:
        try:
            total = ACCOUNT_CASH

            for pos in list(PAPER_POSITIONS.values()):
                qty = safe_float(pos.get("qty"))
                current_price = safe_float(pos.get("current_price_usd"))
                total += qty * current_price

            pnl = total - START_BALANCE
            pnl_pct = (pnl / START_BALANCE) * 100 if START_BALANCE > 0 else 0.0

            if RUN_AUTO_BUY != "on":
                send(
                    f"📊 PAPER PORTFOLIO\n\n"
                    f"Starting Balance ${START_BALANCE:.2f}\n"
                    f"Current Value ${total:.2f}\n\n"
                    f"Total Profit ${pnl:.2f}\n"
                    f"PnL {pnl_pct:.2f}%\n\n"
                    f"Cash ${ACCOUNT_CASH:.2f}\n"
                    f"Open Trades {len(PAPER_POSITIONS)}/{MAX_OPEN_TRADES}"
                )
        except Exception as e:
            print(f"portfolio_loop error: {e}")

        time.sleep(PORTFOLIO_UPDATE_SECONDS)


def live_account_loop():
    while True:
        try:
            if RUN_AUTO_BUY == "on":
                live = get_live_account_snapshot()

                send(
                    f"📈 LIVE ACCOUNT STATUS\n\n"
                    f"Wallet ETH {live['wallet_eth']:.6f}\n"
                    f"ETH USD ${live['eth_usd']:.2f}\n\n"
                    f"Cash Value ${live['cash_value_usd']:.2f}\n"
                    f"Open Position Value ${live['open_position_value_usd']:.2f}\n"
                    f"Total Equity ${live['total_equity_usd']:.2f}\n\n"
                    f"Unrealized PnL ${live['unrealized_pnl_usd']:.2f}\n"
                    f"Unrealized PnL {live['unrealized_pnl_pct']:.2f}%\n\n"
                    f"Open Orders {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}"
                )
        except Exception as e:
            print(f"live_account_loop error: {e}")

        time.sleep(LIVE_ACCOUNT_UPDATE_SECONDS)


def heartbeat_loop():
    while True:
        try:
            extra = ""
            if RUN_AUTO_BUY == "on":
                live = get_live_account_snapshot()
                extra = (
                    f"\nWallet ETH {live['wallet_eth']:.6f}"
                    f"\nCash Value ${live['cash_value_usd']:.2f}"
                    f"\nOpen Position Value ${live['open_position_value_usd']:.2f}"
                    f"\nTotal Live Equity ${live['total_equity_usd']:.2f}"
                    f"\nLive Unrealized PnL ${live['unrealized_pnl_usd']:.2f}"
                    f"\nLive Unrealized PnL {live['unrealized_pnl_pct']:.2f}%"
                )

            send(
                f"💓 BOT HEARTBEAT\n\n"
                f"Connected YES\n"
                f"Block {safe_block_number()}\n"
                f"Mode {'LIVE' if RUN_AUTO_BUY == 'on' else 'PAPER'}\n"
                f"Top Coins Limit {TOP_COINS_LIMIT}\n"
                f"Tracked Histories {len(PRICE_HISTORY)}\n"
                f"Paper Trades {len(PAPER_POSITIONS)}/{MAX_OPEN_TRADES}\n"
                f"Live Positions {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}\n"
                f"Check Every {CHECK_INTERVAL_SECONDS}s\n"
                f"Entry Pump {ENTRY_PUMP_PCT:.2f}%\n"
                f"Trail Arm {TRAIL_ARM_PCT:.2f}%\n"
                f"Trail Drop {TRAIL_DROP_PCT:.2f}%\n"
                f"Stop Loss {STOP_LOSS_PCT:.2f}%"
                f"{extra}"
            )
        except Exception as e:
            print(f"heartbeat_loop error: {e}")

        time.sleep(HEARTBEAT_SECONDS)


# -------------------------
# MAIN
# -------------------------
def main():
    send(
        f"Top 100 Momentum Bot Started\n\n"
        f"Mode {'LIVE' if RUN_AUTO_BUY == 'on' else 'PAPER'}\n"
        f"Buy Size ${BUY_SIZE_USD:.2f}\n"
        f"Max Open Trades {MAX_OPEN_TRADES}\n"
        f"Top Coins Limit {TOP_COINS_LIMIT}\n"
        f"Min 24h Volume ${MIN_24H_VOLUME_USD:,.0f}\n"
        f"Min Market Cap ${MIN_MARKET_CAP_USD:,.0f}\n"
        f"Check Interval {CHECK_INTERVAL_SECONDS}s\n"
        f"Lookback Points {LOOKBACK_POINTS}\n"
        f"Entry Pump {ENTRY_PUMP_PCT:.2f}%\n"
        f"Trail Arm {TRAIL_ARM_PCT:.2f}%\n"
        f"Trail Drop {TRAIL_DROP_PCT:.2f}%\n"
        f"Stop Loss {STOP_LOSS_PCT:.2f}%\n"
        f"Slippage {SLIPPAGE_BPS} bps\n"
        f"Excluded Symbols {','.join(sorted(EXCLUDED_SYMBOLS)) if EXCLUDED_SYMBOLS else 'none'}"
    )

    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=portfolio_loop, daemon=True).start()
    threading.Thread(target=live_account_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    if RUN_AUTO_BUY == "on":
        threading.Thread(target=monitor_live_positions, daemon=True).start()
    else:
        threading.Thread(target=monitor_paper_positions, daemon=True).start()

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
