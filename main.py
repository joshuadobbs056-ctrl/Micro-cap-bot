import os
import sys
import time
import threading
import subprocess
from typing import Optional, Dict, Any, Tuple, List


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
RUN_PURCHASE = os.getenv("RUN_PURCHASE", "off").strip().lower()  # on/off

START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD", "50"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "10"))

PORTFOLIO_UPDATE_SECONDS = int(os.getenv("PORTFOLIO_UPDATE_SECONDS", "60"))
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "600"))
EVENT_POLL_SECONDS = float(os.getenv("EVENT_POLL_SECONDS", "10"))
DISCOVERY_WAIT_SECONDS = int(os.getenv("DISCOVERY_WAIT_SECONDS", "45"))
DISCOVERY_POLL_SECONDS = float(os.getenv("DISCOVERY_POLL_SECONDS", "2"))
POSITION_CHECK_SECONDS = int(os.getenv("POSITION_CHECK_SECONDS", "20"))
LIVE_ACCOUNT_UPDATE_SECONDS = int(os.getenv("LIVE_ACCOUNT_UPDATE_SECONDS", "60"))

# scan recent blocks on startup so the scanner can prove detection quickly
STARTUP_LOOKBACK_BLOCKS = int(os.getenv("STARTUP_LOOKBACK_BLOCKS", "250"))

# widened age window so recent pools can still qualify later
MAX_TOKEN_AGE_SECONDS = int(os.getenv("MAX_TOKEN_AGE_SECONDS", "7200"))  # 120 min

# entry criteria
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "2000"))
MIN_VOLUME_5M_USD = float(os.getenv("MIN_VOLUME_5M_USD", "25"))
MIN_SELLS_5M = int(os.getenv("MIN_SELLS_5M", "0"))
MIN_BUYS_5M = int(os.getenv("MIN_BUYS_5M", "1"))

# tax / sellability filter
MAX_SELL_TAX_PCT = float(os.getenv("MAX_SELL_TAX_PCT", "15"))
MAX_BUY_TAX_PCT = float(os.getenv("MAX_BUY_TAX_PCT", "20"))

# exit rules
LIQUIDITY_DROP_EXIT_PCT = float(os.getenv("LIQUIDITY_DROP_EXIT_PCT", "35"))

# trailing exit
TRAIL_ARM_PCT = float(os.getenv("TRAIL_ARM_PCT", "60"))
TRAIL_DROP_PCT = float(os.getenv("TRAIL_DROP_PCT", "30"))

# paper exit simulation
PAPER_EXIT_HAIRCUT_PCT = float(os.getenv("PAPER_EXIT_HAIRCUT_PCT", "15"))
PAPER_EXIT_MAX_BUYSIDE_SHARE = float(os.getenv("PAPER_EXIT_MAX_BUYSIDE_SHARE", "0.80"))
PAPER_EXIT_MIN_VALUE_USD = float(os.getenv("PAPER_EXIT_MIN_VALUE_USD", "0.01"))
PAPER_MARK_PRICE_FALLBACK_HAIRCUT_PCT = float(os.getenv("PAPER_MARK_PRICE_FALLBACK_HAIRCUT_PCT", "15"))
PAPER_STALE_SNAPSHOT_SECONDS = int(os.getenv("PAPER_STALE_SNAPSHOT_SECONDS", "90"))
PAPER_STALE_MARKDOWN_PCT = float(os.getenv("PAPER_STALE_MARKDOWN_PCT", "35"))

# live buy settings
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "2000"))
GAS_LIMIT_BUY = int(os.getenv("GAS_LIMIT_BUY", "450000"))
GAS_LIMIT_APPROVE = int(os.getenv("GAS_LIMIT_APPROVE", "120000"))
GAS_LIMIT_SELL = int(os.getenv("GAS_LIMIT_SELL", "450000"))
BUY_DEADLINE_SECONDS = int(os.getenv("BUY_DEADLINE_SECONDS", "180"))
SELL_DEADLINE_SECONDS = int(os.getenv("SELL_DEADLINE_SECONDS", "180"))

# v3
V3_DEFAULT_FEE = int(os.getenv("V3_DEFAULT_FEE", "3000"))
GAS_LIMIT_BUY_V3 = int(os.getenv("GAS_LIMIT_BUY_V3", "550000"))
GAS_LIMIT_SELL_V3 = int(os.getenv("GAS_LIMIT_SELL_V3", "550000"))
V3_FEE_CANDIDATES = [500, 3000, 10000]

# live safety / retry
MIN_ETH_GAS_RESERVE = float(os.getenv("MIN_ETH_GAS_RESERVE", "0.01"))
FAILED_BUY_COOLDOWN_SECONDS = int(os.getenv("FAILED_BUY_COOLDOWN_SECONDS", "900"))
PAIR_REJECT_LOG_COOLDOWN_SECONDS = int(os.getenv("PAIR_REJECT_LOG_COOLDOWN_SECONDS", "180"))

# provider limits / retry
MAX_LOG_RANGE = int(os.getenv("MAX_LOG_RANGE", "10"))
RPC_BACKOFF_START_SECONDS = int(os.getenv("RPC_BACKOFF_START_SECONDS", "2"))
RPC_BACKOFF_MAX_SECONDS = int(os.getenv("RPC_BACKOFF_MAX_SECONDS", "30"))

# optional hard cap so listener threads do not get bogged down
MAX_ACTIVE_POOL_THREADS = int(os.getenv("MAX_ACTIVE_POOL_THREADS", "100"))

# recent pool rescanner
RECENT_POOL_RESCAN_SECONDS = int(os.getenv("RECENT_POOL_RESCAN_SECONDS", "15"))
RECENT_POOL_RECHECK_COOLDOWN_SECONDS = int(os.getenv("RECENT_POOL_RECHECK_COOLDOWN_SECONDS", "20"))
RECENT_POOL_MAX_TRACKED = int(os.getenv("RECENT_POOL_MAX_TRACKED", "2000"))


# -------------------------
# WEB3
# -------------------------
if not NODE:
    raise RuntimeError("NODE missing")

if NODE.startswith("wss://"):
    NODE = NODE.replace("wss://", "https://", 1)
    print("Converted WSS node to HTTPS for threaded scanner.")
elif NODE.startswith("ws://"):
    NODE = NODE.replace("ws://", "http://", 1)
    print("Converted WS node to HTTP for threaded scanner.")

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
V2_FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
V3_FACTORY = Web3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")
ROUTER = Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
V3_ROUTER = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")
V3_QUOTER = Web3.to_checksum_address("0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6")
CHAINLINK_ETH_USD = Web3.to_checksum_address("0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419")

V2_FACTORY_ABI = [
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

V3_FACTORY_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": True, "name": "fee", "type": "uint24"},
            {"indexed": False, "name": "tickSpacing", "type": "int24"},
            {"indexed": False, "name": "pool", "type": "address"},
        ],
        "name": "PoolCreated",
        "type": "event",
    }
]

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

v2_factory = w3.eth.contract(address=V2_FACTORY, abi=V2_FACTORY_ABI)
v3_factory = w3.eth.contract(address=V3_FACTORY, abi=V3_FACTORY_ABI)
router = w3.eth.contract(address=ROUTER, abi=ROUTER_ABI)
v3_router = w3.eth.contract(address=V3_ROUTER, abi=V3_ROUTER_ABI)
v3_quoter = w3.eth.contract(address=V3_QUOTER, abi=V3_QUOTER_ABI)
eth_usd_feed = w3.eth.contract(address=CHAINLINK_ETH_USD, abi=CHAINLINK_ETH_USD_ABI)


# -------------------------
# TELEGRAM
# -------------------------
def send(msg: str):
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            SESSION.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "disable_web_page_preview": True},
                timeout=10,
            )
        except Exception:
            pass
    print(msg)


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


def safe_pct(value: Any, default: float = 999.0) -> float:
    try:
        if value in (None, "", "unknown"):
            return default
        return float(value)
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


def wei_to_eth(value_wei: int) -> float:
    return float(w3.from_wei(int(value_wei), "ether"))


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


def get_v3_fee_from_source(source: str) -> int:
    if isinstance(source, str) and source.startswith("V3:"):
        try:
            return int(source.split(":", 1)[1])
        except Exception:
            return V3_DEFAULT_FEE
    return V3_DEFAULT_FEE


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


def get_v3_quote_best(amount_in_wei: int, token: str, preferred_fee: Optional[int] = None) -> Tuple[bool, int, int, int, str]:
    token = Web3.to_checksum_address(token)

    fees_to_try = []
    if preferred_fee and int(preferred_fee) > 0:
        fees_to_try.append(int(preferred_fee))

    for fee in V3_FEE_CANDIDATES:
        if fee not in fees_to_try:
            fees_to_try.append(fee)

    errors = []
    for fee in fees_to_try:
        ok, expected_out, amount_out_min, reason = get_v3_quote(amount_in_wei, token, fee)
        if ok:
            return True, expected_out, amount_out_min, fee, "ok"
        errors.append(f"{fee}: {reason}")

    return False, 0, 0, 0, " | ".join(errors)


def get_live_buy_block_reason(snap: dict) -> Optional[str]:
    age = snap.get("age_seconds")
    liquidity = safe_float(snap.get("liquidity_usd"))
    volume = safe_float(snap.get("volume_5m"))
    buys = int(snap.get("buys_5m") or 0)
    sells = int(snap.get("sells_5m") or 0)
    price = safe_float(snap.get("price_usd"))

    if age is None:
        return "age unknown"
    if age > MAX_TOKEN_AGE_SECONDS:
        return f"age too high: {age:.0f}s > {MAX_TOKEN_AGE_SECONDS}s"
    if liquidity < MIN_LIQUIDITY_USD:
        return f"liquidity too low: ${liquidity:,.0f} < ${MIN_LIQUIDITY_USD:,.0f}"
    if volume < MIN_VOLUME_5M_USD:
        return f"volume too low: ${volume:,.0f} < ${MIN_VOLUME_5M_USD:,.0f}"
    if sells < MIN_SELLS_5M:
        return f"sells too low: {sells} < {MIN_SELLS_5M}"
    if buys < MIN_BUYS_5M:
        return f"buys too low: {buys} < {MIN_BUYS_5M}"
    if price <= 0:
        return "price <= 0"
    return None


def should_log_pair_reason(pair: str) -> bool:
    current = now_ts()
    with LOCK:
        last = PAIR_REASON_LOG_TS.get(pair, 0.0)
        if current - last >= PAIR_REJECT_LOG_COOLDOWN_SECONDS:
            PAIR_REASON_LOG_TS[pair] = current
            return True
    return False


def get_live_position_metrics(pos: dict) -> Dict[str, float]:
    decimals = int(pos.get("decimals", 18))
    token_amount_raw = int(pos.get("token_amount_raw", 0))
    token_amount = token_amount_raw / (10 ** decimals) if decimals >= 0 else 0.0

    entry_value_usd = safe_float(pos.get("entry_value_usd"), PURCHASE_AMOUNT_USD)
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

    for pos in LIVE_POSITIONS.values():
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
# SECURITY / TAX CHECK
# -------------------------
def get_token_security(token: str) -> Optional[dict]:
    try:
        url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
        r = SESSION.get(url, timeout=10)

        if r.status_code != 200:
            return None

        body = (r.text or "").strip()
        if not body:
            return None

        data = r.json()
        result = (data.get("result") or {}).get(token.lower())
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def passes_tax_and_sellability(token: str) -> Tuple[bool, str]:
    sec = get_token_security(token)
    if not sec:
        return True, "security unavailable"

    if sec.get("is_honeypot") == "1":
        return False, "honeypot flagged"

    if sec.get("cannot_sell_all") == "1":
        return False, "cannot sell all flagged"

    sell_tax_raw = sec.get("sell_tax")
    buy_tax_raw = sec.get("buy_tax")

    sell_tax = safe_pct(sell_tax_raw, default=-1.0)
    buy_tax = safe_pct(buy_tax_raw, default=-1.0)

    if sell_tax >= 0 and sell_tax > MAX_SELL_TAX_PCT:
        return False, f"sell tax too high: {sell_tax:.2f}%"

    if buy_tax >= 0 and buy_tax > MAX_BUY_TAX_PCT:
        return False, f"buy tax too high: {buy_tax:.2f}%"

    sell_text = "unknown" if sell_tax < 0 else f"{sell_tax:.2f}%"
    buy_text = "unknown" if buy_tax < 0 else f"{buy_tax:.2f}%"

    return True, f"buy tax {buy_text} | sell tax {sell_text}"


# -------------------------
# DEXSCREENER SNAPSHOT
# -------------------------
def get_pair_snapshot(pair: str) -> Optional[Dict[str, Any]]:
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/ethereum/{pair}"
        r = SESSION.get(url, timeout=10)

        if r.status_code != 200:
            print(f"get_pair_snapshot status {r.status_code} for {pair}")
            return None

        body = (r.text or "").strip()
        if not body:
            print(f"get_pair_snapshot empty response for {pair}")
            return None

        try:
            data = r.json()
        except Exception:
            preview = body[:200].replace("\n", " ")
            print(f"get_pair_snapshot non-json response for {pair}: {preview}")
            return None

        p = data.get("pair")
        if not p:
            pairs = data.get("pairs") or []
            if pairs:
                p = pairs[0]

        if not p:
            return None

        created_ms = p.get("pairCreatedAt")
        age_seconds = None
        if created_ms:
            age_seconds = (now_ts() * 1000 - float(created_ms)) / 1000.0

        txns_5m = (p.get("txns") or {}).get("m5") or {}
        liquidity = p.get("liquidity") or {}
        volume = p.get("volume") or {}
        base = p.get("baseToken") or {}

        return {
            "pair": p.get("pairAddress"),
            "token": base.get("address") or "",
            "symbol": base.get("symbol") or "UNK",
            "price_usd": safe_float(p.get("priceUsd")),
            "liquidity_usd": safe_float(liquidity.get("usd")),
            "volume_5m": safe_float(volume.get("m5")),
            "buys_5m": int(txns_5m.get("buys") or 0),
            "sells_5m": int(txns_5m.get("sells") or 0),
            "fdv": safe_float(p.get("fdv")),
            "age_seconds": age_seconds,
            "url": p.get("url", ""),
        }

    except requests.exceptions.Timeout:
        print(f"get_pair_snapshot timeout for {pair}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"get_pair_snapshot request error for {pair}: {e}")
        return None
    except Exception as e:
        print(f"get_pair_snapshot error for {pair}: {e}")
        return None


# -------------------------
# LIVE BUY/SELL
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


def execute_live_buy_v2(
    token: str,
    pair: str,
    entry_liquidity_usd: float,
    entry_price: float,
    source: str,
) -> Optional[dict]:
    if not ACCOUNT:
        send("⚠️ LIVE BUY SKIPPED\nReason: PRIVATE_KEY not loaded")
        return None

    wallet = ACCOUNT.address
    token = Web3.to_checksum_address(token)
    _, symbol, decimals = get_token_meta(token)

    with LOCK:
        last_fail = FAILED_LIVE_BUYS.get(pair, 0.0)
    if now_ts() - last_fail < FAILED_BUY_COOLDOWN_SECONDS:
        remaining = int(FAILED_BUY_COOLDOWN_SECONDS - (now_ts() - last_fail))
        send(
            f"⚠️ LIVE BUY SKIPPED\n\n"
            f"{symbol}\n"
            f"Pair\n{pair}\n\n"
            f"Reason: failed buy cooldown active\n"
            f"Retry In {remaining}s"
        )
        return None

    try:
        eth_amount = usd_to_eth(PURCHASE_AMOUNT_USD)
        value_wei = int(w3.to_wei(eth_amount, "ether"))
        path = [WETH, token]

        wallet_balance = int(w3.eth.get_balance(wallet))
        total_required_wei = estimate_total_buy_cost_wei(value_wei, GAS_LIMIT_BUY)

        if wallet_balance < total_required_wei:
            send(
                f"⚠️ LIVE BUY SKIPPED\n\n"
                f"{symbol}\n"
                f"Pair\n{pair}\n\n"
                f"Reason: insufficient ETH for buy + gas reserve\n"
                f"Wallet ETH {wei_to_eth(wallet_balance):.6f}\n"
                f"Needed ETH {wei_to_eth(total_required_wei):.6f}"
            )
            return None

        quote_ok, expected_out, amount_out_min, quote_reason = get_v2_quote(value_wei, token)
        if not quote_ok:
            with LOCK:
                FAILED_LIVE_BUYS[pair] = now_ts()
            send(
                f"⚠️ LIVE BUY SKIPPED\n\n"
                f"{symbol}\n"
                f"Source {source}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Reason: {quote_reason}"
            )
            return None

        token_contract = get_token_contract(token)
        balance_before = int(token_contract.functions.balanceOf(wallet).call())

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        deadline = int(time.time()) + BUY_DEADLINE_SECONDS

        tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            amount_out_min,
            path,
            wallet,
            deadline,
        ).build_transaction(build_tx_params(wallet, nonce, GAS_LIMIT_BUY, value=value_wei))

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status != 1:
            with LOCK:
                FAILED_LIVE_BUYS[pair] = now_ts()
            send(
                f"❌ LIVE BUY FAILED\n\n"
                f"{symbol}\n"
                f"Source {source}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Tx {tx_hash.hex()}\n"
                f"Reason: transaction reverted"
            )
            return None

        balance_after = int(token_contract.functions.balanceOf(wallet).call())
        token_amount_raw = max(balance_after - balance_before, 0)

        if token_amount_raw <= 0:
            with LOCK:
                FAILED_LIVE_BUYS[pair] = now_ts()
            send(
                f"❌ LIVE BUY FAILED\n\n"
                f"{symbol}\n"
                f"Source {source}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Tx {tx_hash.hex()}\n"
                f"Reason: no token balance received"
            )
            return None

        token_amount = token_amount_raw / (10 ** decimals)

        send(
            f"🟢 LIVE BUY OPENED\n\n"
            f"{symbol}\n"
            f"Source {source}\n"
            f"Token\n{token}\n\n"
            f"Pair\n{pair}\n\n"
            f"Entry Price ${entry_price:.10f}\n"
            f"Entry Liquidity ${entry_liquidity_usd:,.0f}\n"
            f"Buy Size ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH {eth_amount:.6f}\n"
            f"Quoted Tokens {expected_out / (10 ** decimals):,.6f}\n"
            f"Received Tokens {token_amount:,.6f}\n"
            f"Tx {tx_hash.hex()}"
        )

        return {
            "token": token,
            "pair": pair,
            "symbol": symbol,
            "entry_price": entry_price,
            "entry_liquidity_usd": entry_liquidity_usd,
            "token_amount_raw": token_amount_raw,
            "decimals": decimals,
            "opened": now_ts(),
            "peak_price": entry_price if entry_price > 0 else 0.0,
            "source": "V2",
            "entry_value_usd": PURCHASE_AMOUNT_USD,
            "current_price_usd": entry_price if entry_price > 0 else 0.0,
            "current_liquidity_usd": entry_liquidity_usd,
        }

    except Exception as e:
        with LOCK:
            FAILED_LIVE_BUYS[pair] = now_ts()
        send(
            f"❌ LIVE BUY ERROR\n\n"
            f"{symbol}\n"
            f"Source {source}\n"
            f"Token\n{token}\n\n"
            f"Pair\n{pair}\n\n"
            f"{e}"
        )
        return None


def execute_live_buy_v3(
    token: str,
    pair: str,
    entry_liquidity_usd: float,
    entry_price: float,
    source: str,
) -> Optional[dict]:
    if not ACCOUNT:
        send("⚠️ LIVE BUY SKIPPED\nReason: PRIVATE_KEY not loaded")
        return None

    wallet = ACCOUNT.address
    token = Web3.to_checksum_address(token)
    _, symbol, decimals = get_token_meta(token)
    preferred_fee = get_v3_fee_from_source(source)

    with LOCK:
        last_fail = FAILED_LIVE_BUYS.get(pair, 0.0)
    if now_ts() - last_fail < FAILED_BUY_COOLDOWN_SECONDS:
        remaining = int(FAILED_BUY_COOLDOWN_SECONDS - (now_ts() - last_fail))
        send(
            f"⚠️ LIVE BUY SKIPPED\n\n"
            f"{symbol}\n"
            f"Pair\n{pair}\n\n"
            f"Reason: failed buy cooldown active\n"
            f"Retry In {remaining}s"
        )
        return None

    try:
        eth_amount = usd_to_eth(PURCHASE_AMOUNT_USD)
        value_wei = int(w3.to_wei(eth_amount, "ether"))

        wallet_balance = int(w3.eth.get_balance(wallet))
        total_required_wei = estimate_total_buy_cost_wei(value_wei, GAS_LIMIT_BUY_V3)

        if wallet_balance < total_required_wei:
            send(
                f"⚠️ LIVE BUY SKIPPED\n\n"
                f"{symbol}\n"
                f"Pair\n{pair}\n\n"
                f"Reason: insufficient ETH for buy + gas reserve\n"
                f"Wallet ETH {wei_to_eth(wallet_balance):.6f}\n"
                f"Needed ETH {wei_to_eth(total_required_wei):.6f}"
            )
            return None

        quote_ok, expected_out, amount_out_min, fee_used, quote_reason = get_v3_quote_best(
            value_wei,
            token,
            preferred_fee=preferred_fee,
        )
        if not quote_ok:
            with LOCK:
                FAILED_LIVE_BUYS[pair] = now_ts()
            send(
                f"⚠️ LIVE BUY SKIPPED\n\n"
                f"{symbol}\n"
                f"Source {source}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Reason: {quote_reason}"
            )
            return None

        token_contract = get_token_contract(token)
        balance_before = int(token_contract.functions.balanceOf(wallet).call())

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        deadline = int(time.time()) + BUY_DEADLINE_SECONDS

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
                FAILED_LIVE_BUYS[pair] = now_ts()
            send(
                f"❌ LIVE BUY FAILED\n\n"
                f"{symbol}\n"
                f"Source {source}\n"
                f"Fee Tier {fee_used}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Tx {tx_hash.hex()}\n"
                f"Reason: V3 transaction reverted"
            )
            return None

        balance_after = int(token_contract.functions.balanceOf(wallet).call())
        token_amount_raw = max(balance_after - balance_before, 0)

        if token_amount_raw <= 0:
            with LOCK:
                FAILED_LIVE_BUYS[pair] = now_ts()
            send(
                f"❌ LIVE BUY FAILED\n\n"
                f"{symbol}\n"
                f"Source {source}\n"
                f"Fee Tier {fee_used}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Tx {tx_hash.hex()}\n"
                f"Reason: no token balance received"
            )
            return None

        token_amount = token_amount_raw / (10 ** decimals)

        send(
            f"🟢 LIVE BUY OPENED (V3)\n\n"
            f"{symbol}\n"
            f"Source {source}\n"
            f"Fee Tier {fee_used}\n"
            f"Token\n{token}\n\n"
            f"Pair\n{pair}\n\n"
            f"Entry Price ${entry_price:.10f}\n"
            f"Entry Liquidity ${entry_liquidity_usd:,.0f}\n"
            f"Buy Size ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH {eth_amount:.6f}\n"
            f"Quoted Tokens {expected_out / (10 ** decimals):,.6f}\n"
            f"Received Tokens {token_amount:,.6f}\n"
            f"Tx {tx_hash.hex()}"
        )

        return {
            "token": token,
            "pair": pair,
            "symbol": symbol,
            "entry_price": entry_price,
            "entry_liquidity_usd": entry_liquidity_usd,
            "token_amount_raw": token_amount_raw,
            "decimals": decimals,
            "opened": now_ts(),
            "peak_price": entry_price if entry_price > 0 else 0.0,
            "source": f"V3:{fee_used}",
            "entry_value_usd": PURCHASE_AMOUNT_USD,
            "current_price_usd": entry_price if entry_price > 0 else 0.0,
            "current_liquidity_usd": entry_liquidity_usd,
        }

    except Exception as e:
        with LOCK:
            FAILED_LIVE_BUYS[pair] = now_ts()
        send(
            f"❌ LIVE BUY ERROR (V3)\n\n"
            f"{symbol}\n"
            f"Source {source}\n"
            f"Token\n{token}\n\n"
            f"Pair\n{pair}\n\n"
            f"{e}"
        )
        return None


def execute_live_buy(
    token: str,
    pair: str,
    entry_liquidity_usd: float,
    entry_price: float,
    source: str,
) -> Optional[dict]:
    if isinstance(source, str) and source.startswith("V3"):
        return execute_live_buy_v3(token, pair, entry_liquidity_usd, entry_price, source)
    return execute_live_buy_v2(token, pair, entry_liquidity_usd, entry_price, source)


def execute_live_sell_v2(position: dict, current_price_usd: float, current_liquidity_usd: float, reason: str) -> bool:
    if not ACCOUNT:
        return False

    token = position["token"]
    pair = position["pair"]
    symbol = position["symbol"]
    amount_raw = position["token_amount_raw"]
    wallet = ACCOUNT.address
    m = get_live_position_metrics(position)

    try:
        if amount_raw <= 0:
            return False
        if not approve_token_if_needed(token, amount_raw, ROUTER):
            return False

        path = [token, WETH]
        try:
            amounts_out = router.functions.getAmountsOut(amount_raw, path).call()
            expected_eth_out = int(amounts_out[-1])
            amount_out_min = int(expected_eth_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception as e:
            send(
                f"❌ LIVE SELL SKIPPED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Reason: no usable V2 sell route\n{e}"
            )
            return False

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        deadline = int(time.time()) + SELL_DEADLINE_SECONDS

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
            send(
                f"🔴 LIVE SELL CLOSED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Tokens Held {m['token_amount']:,.6f}\n"
                f"Entry Value ${m['entry_value_usd']:.2f}\n"
                f"Current Value ${m['current_value_usd']:.2f}\n"
                f"PnL ${m['pnl_usd']:.2f}\n"
                f"PnL {m['pnl_pct']:.2f}%\n\n"
                f"Current Price ${current_price_usd:.10f}\n"
                f"Current Liquidity ${current_liquidity_usd:,.0f}\n"
                f"Open Orders {max(len(LIVE_POSITIONS) - 1, 0)}/{MAX_OPEN_TRADES}\n"
                f"Reason: {reason}\n"
                f"Tx {tx_hash.hex()}"
            )
            return True

        send(
            f"❌ LIVE SELL FAILED\n\n"
            f"{symbol}\n"
            f"Token\n{token}\n\n"
            f"Pair\n{pair}\n\n"
            f"Tokens Held {m['token_amount']:,.6f}\n"
            f"Entry Value ${m['entry_value_usd']:.2f}\n"
            f"Current Value ${m['current_value_usd']:.2f}\n"
            f"PnL ${m['pnl_usd']:.2f}\n"
            f"PnL {m['pnl_pct']:.2f}%\n"
            f"Tx {tx_hash.hex()}"
        )
        return False

    except Exception as e:
        send(f"❌ LIVE SELL ERROR\n{symbol}\n{token}\n{e}")
        return False


def execute_live_sell_v3(position: dict, current_price_usd: float, current_liquidity_usd: float, reason: str) -> bool:
    if not ACCOUNT:
        return False

    token = position["token"]
    pair = position["pair"]
    symbol = position["symbol"]
    amount_raw = position["token_amount_raw"]
    wallet = ACCOUNT.address
    source = str(position.get("source", "V3"))
    fee = get_v3_fee_from_source(source)
    m = get_live_position_metrics(position)

    try:
        if amount_raw <= 0:
            return False
        if not approve_token_if_needed(token, amount_raw, V3_ROUTER):
            return False

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
        except Exception as e:
            send(
                f"❌ LIVE SELL SKIPPED\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Reason: no usable V3 sell route\n{e}"
            )
            return False

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        deadline = int(time.time()) + SELL_DEADLINE_SECONDS

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

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt.status == 1:
            send(
                f"🔴 LIVE SELL CLOSED (V3)\n\n"
                f"{symbol}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Tokens Held {m['token_amount']:,.6f}\n"
                f"Entry Value ${m['entry_value_usd']:.2f}\n"
                f"Current Value ${m['current_value_usd']:.2f}\n"
                f"PnL ${m['pnl_usd']:.2f}\n"
                f"PnL {m['pnl_pct']:.2f}%\n\n"
                f"Fee Tier {fee}\n"
                f"Current Price ${current_price_usd:.10f}\n"
                f"Current Liquidity ${current_liquidity_usd:,.0f}\n"
                f"Open Orders {max(len(LIVE_POSITIONS) - 1, 0)}/{MAX_OPEN_TRADES}\n"
                f"Reason: {reason}\n"
                f"Tx {tx_hash.hex()}"
            )
            return True

        send(
            f"❌ LIVE SELL FAILED (V3)\n\n"
            f"{symbol}\n"
            f"Token\n{token}\n\n"
            f"Pair\n{pair}\n\n"
            f"Tokens Held {m['token_amount']:,.6f}\n"
            f"Entry Value ${m['entry_value_usd']:.2f}\n"
            f"Current Value ${m['current_value_usd']:.2f}\n"
            f"PnL ${m['pnl_usd']:.2f}\n"
            f"PnL {m['pnl_pct']:.2f}%\n"
            f"Tx {tx_hash.hex()}"
        )
        return False

    except Exception as e:
        send(f"❌ LIVE SELL ERROR (V3)\n{symbol}\n{token}\n{e}")
        return False


def execute_live_sell(position: dict, current_price_usd: float, current_liquidity_usd: float, reason: str) -> bool:
    source = str(position.get("source", "V2"))
    if source.startswith("V3"):
        return execute_live_sell_v3(position, current_price_usd, current_liquidity_usd, reason)
    return execute_live_sell_v2(position, current_price_usd, current_liquidity_usd, reason)


# -------------------------
# PAPER TRADE MODEL
# -------------------------
ACCOUNT_CASH = START_BALANCE


class PaperTrade:
    def __init__(self, token: str, pair: str, symbol: str, entry_price: float, entry_liquidity_usd: float):
        self.token = token
        self.pair = pair
        self.symbol = symbol
        self.entry_price = entry_price
        self.entry_liquidity_usd = entry_liquidity_usd
        self.tokens = PURCHASE_AMOUNT_USD / entry_price if entry_price > 0 else 0.0
        self.opened = now_ts()

        self.last_good_price = entry_price if entry_price > 0 else 0.0
        self.last_good_liquidity_usd = entry_liquidity_usd if entry_liquidity_usd > 0 else 0.0
        self.last_mark_value_usd = PURCHASE_AMOUNT_USD
        self.peak_value_usd = PURCHASE_AMOUNT_USD
        self.peak_price = entry_price if entry_price > 0 else 0.0
        self.last_update_ts = now_ts()
        self.last_good_snapshot_ts = now_ts()


PAPER_TRADES: Dict[str, PaperTrade] = {}
LIVE_POSITIONS: Dict[str, dict] = {}

ACTIVE_POOLS = set()
ALERTED_POOLS = set()
TRADED_POOLS = set()

RECENT_POOLS: Dict[str, Dict[str, Any]] = {}
FAILED_LIVE_BUYS: Dict[str, float] = {}
PAIR_REASON_LOG_TS: Dict[str, float] = {}

LOCK = threading.Lock()
POOL_THREAD_SEMAPHORE = threading.BoundedSemaphore(MAX_ACTIVE_POOL_THREADS)


def update_trade_market_state(trade: PaperTrade, price: float, liquidity_usd: float):
    good_snapshot = False

    if price > 0:
        trade.last_good_price = price
        good_snapshot = True

    if liquidity_usd >= 0:
        trade.last_good_liquidity_usd = liquidity_usd
        good_snapshot = True

    mark_price = price if price > 0 else trade.last_good_price
    if mark_price > 0:
        trade.last_mark_value_usd = trade.tokens * mark_price
        if trade.last_mark_value_usd > trade.peak_value_usd:
            trade.peak_value_usd = trade.last_mark_value_usd
        if mark_price > trade.peak_price:
            trade.peak_price = mark_price

    trade.last_update_ts = now_ts()
    if good_snapshot:
        trade.last_good_snapshot_ts = now_ts()


def simulate_paper_exit(trade: PaperTrade, snap: Optional[Dict[str, Any]]) -> Tuple[float, float, float, str]:
    current_price = 0.0
    current_liq = -1.0

    if snap:
        current_price = safe_float(snap.get("price_usd"))
        current_liq = safe_float(snap.get("liquidity_usd"), default=-1.0)

    usable_price = current_price if current_price > 0 else trade.last_good_price
    usable_liq = current_liq if current_liq >= 0 else trade.last_good_liquidity_usd

    if usable_price <= 0:
        return 0.0, 0.0, -100.0, "no usable price or liquidity basis"

    gross_value = trade.tokens * usable_price

    if current_price <= 0:
        gross_value *= (1.0 - PAPER_MARK_PRICE_FALLBACK_HAIRCUT_PCT / 100.0)

    buy_side_liquidity = max(usable_liq * 0.5, 0.0)
    liquidity_cap = buy_side_liquidity * max(PAPER_EXIT_MAX_BUYSIDE_SHARE, 0.0)

    haircut_value = gross_value * (1.0 - PAPER_EXIT_HAIRCUT_PCT / 100.0)

    if liquidity_cap > 0:
        final_value = min(haircut_value, liquidity_cap)
        cap_text = f"haircut {PAPER_EXIT_HAIRCUT_PCT:.0f}% | cap {PAPER_EXIT_MAX_BUYSIDE_SHARE * 100:.0f}% buy-side"
    else:
        final_value = haircut_value
        cap_text = f"haircut {PAPER_EXIT_HAIRCUT_PCT:.0f}% | no liq cap available"

    if final_value <= 0 and gross_value > 0:
        final_value = max(PAPER_EXIT_MIN_VALUE_USD, gross_value * 0.05)
        cap_text += " | min emergency floor used"

    pnl_pct = ((final_value - PURCHASE_AMOUNT_USD) / PURCHASE_AMOUNT_USD) * 100 if PURCHASE_AMOUNT_USD > 0 else 0.0
    return usable_price, final_value, pnl_pct, cap_text


def get_portfolio_mark_value(trade: PaperTrade) -> float:
    age_of_last_good = now_ts() - trade.last_good_snapshot_ts
    mark_price = trade.last_good_price

    if mark_price <= 0:
        return 0.0

    value = trade.tokens * mark_price

    if age_of_last_good > PAPER_STALE_SNAPSHOT_SECONDS:
        value *= (1.0 - PAPER_STALE_MARKDOWN_PCT / 100.0)

    return value


# -------------------------
# RECENT POOL TRACKING
# -------------------------
def register_recent_pool(token: str, pair: str, source: str):
    with LOCK:
        if pair not in RECENT_POOLS and len(RECENT_POOLS) >= RECENT_POOL_MAX_TRACKED:
            oldest_pair = min(
                RECENT_POOLS.keys(),
                key=lambda p: RECENT_POOLS[p].get("first_seen_ts", now_ts())
            )
            RECENT_POOLS.pop(oldest_pair, None)

        RECENT_POOLS[pair] = {
            "token": Web3.to_checksum_address(token),
            "pair": Web3.to_checksum_address(pair),
            "source": source,
            "first_seen_ts": RECENT_POOLS.get(pair, {}).get("first_seen_ts", now_ts()),
            "last_attempt_ts": RECENT_POOLS.get(pair, {}).get("last_attempt_ts", 0.0),
            "last_snapshot_ts": RECENT_POOLS.get(pair, {}).get("last_snapshot_ts", 0.0),
        }


def cleanup_recent_pools():
    cutoff = now_ts() - MAX_TOKEN_AGE_SECONDS - 300
    with LOCK:
        stale_pairs = []
        for pair, meta in RECENT_POOLS.items():
            first_seen_ts = meta.get("first_seen_ts", 0.0)
            if first_seen_ts and first_seen_ts < cutoff:
                stale_pairs.append(pair)

        for pair in stale_pairs:
            RECENT_POOLS.pop(pair, None)


def get_recent_pool_batch() -> List[Tuple[str, Dict[str, Any]]]:
    with LOCK:
        items = list(RECENT_POOLS.items())
    items.sort(key=lambda x: x[1].get("first_seen_ts", 0.0), reverse=True)
    return items


# -------------------------
# POSITION MONITORS
# -------------------------
def monitor_paper_trade(token: str):
    global ACCOUNT_CASH

    while token in PAPER_TRADES:
        time.sleep(POSITION_CHECK_SECONDS)

        trade = PAPER_TRADES.get(token)
        if not trade:
            return

        snap = get_pair_snapshot(trade.pair)

        if snap:
            price = safe_float(snap["price_usd"])
            current_liq = safe_float(snap["liquidity_usd"], default=-1.0)
            update_trade_market_state(trade, price, current_liq)
            mark_price = price if price > 0 else trade.last_good_price
            display_liq = current_liq
        else:
            mark_price = trade.last_good_price
            current_liq = -1.0
            display_liq = trade.last_good_liquidity_usd

        value = trade.tokens * mark_price if mark_price > 0 else 0.0
        pnl = ((value - PURCHASE_AMOUNT_USD) / PURCHASE_AMOUNT_USD) * 100 if PURCHASE_AMOUNT_USD > 0 else 0.0

        liq_text = f"${display_liq:,.0f}" if display_liq >= 0 else "unknown"

        send(
            f"📊 PAPER TRADE UPDATE\n\n"
            f"{trade.symbol}\n"
            f"Token\n{trade.token}\n\n"
            f"Entry ${trade.entry_price:.10f}\n"
            f"Current ${mark_price:.10f}\n\n"
            f"Entry Liquidity ${trade.entry_liquidity_usd:,.0f}\n"
            f"Current Liquidity {liq_text}\n\n"
            f"Value ${value:.2f}\n"
            f"PnL {pnl:.2f}%"
        )

        trail_arm_value = PURCHASE_AMOUNT_USD * (1.0 + TRAIL_ARM_PCT / 100.0)
        trail_floor_value = trade.peak_value_usd * (1.0 - TRAIL_DROP_PCT / 100.0)
        trailing_armed = trade.peak_value_usd >= trail_arm_value
        trailing_trigger = trailing_armed and value <= trail_floor_value

        if trailing_trigger:
            ACCOUNT_CASH += value
            peak_pnl = ((trade.peak_value_usd - PURCHASE_AMOUNT_USD) / PURCHASE_AMOUNT_USD) * 100 if PURCHASE_AMOUNT_USD > 0 else 0.0

            send(
                f"💰 PAPER TRAILING EXIT\n\n"
                f"{trade.symbol}\n"
                f"Token\n{trade.token}\n\n"
                f"Entry ${trade.entry_price:.10f}\n"
                f"Exit ${mark_price:.10f}\n\n"
                f"Peak Value ${trade.peak_value_usd:.2f}\n"
                f"Final Value ${value:.2f}\n"
                f"PnL {pnl:.2f}%\n\n"
                f"Reason: trailed {TRAIL_DROP_PCT:.0f}% from peak after arming at {TRAIL_ARM_PCT:.0f}%\n"
                f"Peak PnL {peak_pnl:.2f}%"
            )
            PAPER_TRADES.pop(token, None)
            return

        exit_floor = trade.entry_liquidity_usd * (1 - LIQUIDITY_DROP_EXIT_PCT / 100.0)

        liq_trigger = current_liq >= 0 and current_liq <= exit_floor
        stale_and_weak = (
            (now_ts() - trade.last_good_snapshot_ts > PAPER_STALE_SNAPSHOT_SECONDS)
            and trade.last_good_liquidity_usd <= exit_floor
        )

        if liq_trigger or stale_and_weak:
            exit_price_used, final_value, final_pnl_pct, exit_detail = simulate_paper_exit(trade, snap)
            ACCOUNT_CASH += final_value

            reason = (
                f"liquidity dropped {LIQUIDITY_DROP_EXIT_PCT:.0f}%"
                if liq_trigger
                else "stale snapshot + last liquidity below exit floor"
            )

            send(
                f"🧪 PAPER TRADE CLOSED\n\n"
                f"{trade.symbol}\n"
                f"Token\n{trade.token}\n\n"
                f"Entry ${trade.entry_price:.10f}\n"
                f"Sim Exit ${exit_price_used:.10f}\n\n"
                f"Peak Value ${trade.peak_value_usd:.2f}\n"
                f"Final Value ${final_value:.2f}\n"
                f"PnL {final_pnl_pct:.2f}%\n\n"
                f"Reason: {reason}\n"
                f"Model: {exit_detail}"
            )
            PAPER_TRADES.pop(token, None)
            return


def monitor_live_position(token: str):
    while token in LIVE_POSITIONS:
        time.sleep(POSITION_CHECK_SECONDS)

        pos = LIVE_POSITIONS.get(token)
        if not pos:
            return

        snap = get_pair_snapshot(pos["pair"])
        if not snap:
            continue

        current_liq = safe_float(snap["liquidity_usd"])
        current_price = safe_float(snap["price_usd"])
        entry_price = safe_float(pos.get("entry_price"))
        exit_floor = pos["entry_liquidity_usd"] * (1 - LIQUIDITY_DROP_EXIT_PCT / 100.0)

        pos["current_price_usd"] = current_price
        pos["current_liquidity_usd"] = current_liq

        if current_price > pos.get("peak_price", 0.0):
            pos["peak_price"] = current_price

        peak_price = safe_float(pos.get("peak_price"))
        m = get_live_position_metrics(pos)

        send(
            f"📊 LIVE POSITION UPDATE\n\n"
            f"{pos['symbol']}\n"
            f"Token\n{pos['token']}\n\n"
            f"Entry Price ${entry_price:.10f}\n"
            f"Current Price ${current_price:.10f}\n"
            f"Peak Price ${peak_price:.10f}\n\n"
            f"Tokens Held {m['token_amount']:,.6f}\n"
            f"Entry Value ${m['entry_value_usd']:.2f}\n"
            f"Current Value ${m['current_value_usd']:.2f}\n"
            f"PnL ${m['pnl_usd']:.2f}\n"
            f"PnL {m['pnl_pct']:.2f}%\n"
            f"Peak PnL ${m['peak_pnl_usd']:.2f}\n"
            f"Peak PnL {m['peak_pnl_pct']:.2f}%\n\n"
            f"Entry Liquidity ${pos['entry_liquidity_usd']:,.0f}\n"
            f"Current Liquidity ${current_liq:,.0f}\n"
            f"Open Orders {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}"
        )

        if entry_price > 0 and current_price > 0 and peak_price > 0:
            peak_pnl = ((peak_price - entry_price) / entry_price) * 100.0
            trail_floor_price = peak_price * (1.0 - TRAIL_DROP_PCT / 100.0)
            trailing_trigger = peak_pnl >= TRAIL_ARM_PCT and current_price <= trail_floor_price

            if trailing_trigger:
                sold = execute_live_sell(
                    pos,
                    current_price,
                    current_liq,
                    f"trailed {TRAIL_DROP_PCT:.0f}% from peak after arming at {TRAIL_ARM_PCT:.0f}%"
                )
                if sold:
                    LIVE_POSITIONS.pop(token, None)
                    return
                send(
                    f"⚠️ LIVE POSITION STILL OPEN\n\n"
                    f"{pos['symbol']}\n"
                    f"Token\n{pos['token']}\n\n"
                    f"Sell failed, keeping position in memory."
                )

        if current_liq <= exit_floor:
            sold = execute_live_sell(
                pos,
                current_price,
                current_liq,
                f"liquidity dropped {LIQUIDITY_DROP_EXIT_PCT:.0f}%"
            )
            if sold:
                LIVE_POSITIONS.pop(token, None)
                return
            send(
                f"⚠️ LIVE POSITION STILL OPEN\n\n"
                f"{pos['symbol']}\n"
                f"Token\n{pos['token']}\n\n"
                f"Sell failed, keeping position in memory."
            )


# -------------------------
# OPEN TRADE
# -------------------------
def open_paper_trade(token: str, pair: str, snap: dict) -> bool:
    global ACCOUNT_CASH

    if len(PAPER_TRADES) >= MAX_OPEN_TRADES:
        send(f"⚠️ TRADE SKIPPED\nReason: max open trades reached\nOpen Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}")
        return False

    if ACCOUNT_CASH < PURCHASE_AMOUNT_USD:
        send(f"⚠️ TRADE SKIPPED\nReason: insufficient balance\nCash ${ACCOUNT_CASH:.2f}\nRequired ${PURCHASE_AMOUNT_USD:.2f}")
        return False

    symbol = snap["symbol"] or "UNK"
    price = snap["price_usd"]
    liq = snap["liquidity_usd"]

    if price <= 0:
        send(f"⚠️ TRADE SKIPPED\n{symbol}\nReason: no entry price")
        return False

    trade = PaperTrade(token, pair, symbol, price, liq)
    PAPER_TRADES[token] = trade
    ACCOUNT_CASH -= PURCHASE_AMOUNT_USD

    send(
        f"🧪 PAPER TRADE OPENED\n\n"
        f"{symbol}\n"
        f"Token\n{token}\n\n"
        f"Pair\n{pair}\n\n"
        f"Entry Price ${price:.10f}\n"
        f"Entry Liquidity ${liq:,.0f}\n"
        f"Buy Size ${PURCHASE_AMOUNT_USD:.2f}\n"
        f"Tokens {trade.tokens:,.2f}\n"
        f"Open Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}"
    )

    threading.Thread(target=monitor_paper_trade, args=(token,), daemon=True).start()
    return True


# -------------------------
# PROCESS POOL
# -------------------------
def qualifies_for_entry(snap: dict) -> bool:
    return get_live_buy_block_reason(snap) is None


def process_pool(token: str, pair: str, source: str):
    acquired = False
    try:
        acquired = POOL_THREAD_SEMAPHORE.acquire(timeout=1)
        if not acquired:
            return

        with LOCK:
            if pair in ACTIVE_POOLS or pair in TRADED_POOLS:
                return
            ACTIVE_POOLS.add(pair)

        snap = None
        started = now_ts()

        while now_ts() - started < DISCOVERY_WAIT_SECONDS:
            snap = get_pair_snapshot(pair)
            if snap and qualifies_for_entry(snap):
                break

            if snap:
                reason = get_live_buy_block_reason(snap)
                if reason and should_log_pair_reason(pair):
                    send(
                        f"⏳ POOL NOT READY\n\n"
                        f"Source {source}\n"
                        f"Pair\n{pair}\n\n"
                        f"Reason: {reason}\n"
                        f"Liquidity ${safe_float(snap.get('liquidity_usd')):,.0f}\n"
                        f"Volume 5m ${safe_float(snap.get('volume_5m')):,.0f}\n"
                        f"Buys 5m {int(snap.get('buys_5m') or 0)}\n"
                        f"Sells 5m {int(snap.get('sells_5m') or 0)}"
                    )

            time.sleep(DISCOVERY_POLL_SECONDS)

        if not snap:
            snap = get_pair_snapshot(pair)

        if not snap:
            if should_log_pair_reason(pair):
                send(
                    f"⚠️ POOL SKIPPED\n\n"
                    f"Source {source}\n"
                    f"Pair\n{pair}\n\n"
                    f"Reason: snapshot unavailable"
                )
            return

        reason = get_live_buy_block_reason(snap)
        if reason:
            if should_log_pair_reason(pair):
                send(
                    f"⚠️ POOL FILTERED\n\n"
                    f"Source {source}\n"
                    f"{snap.get('symbol', 'UNK')}\n"
                    f"Token\n{token}\n\n"
                    f"Pair\n{pair}\n\n"
                    f"Reason: {reason}\n"
                    f"Liquidity ${safe_float(snap.get('liquidity_usd')):,.0f}\n"
                    f"Volume 5m ${safe_float(snap.get('volume_5m')):,.0f}\n"
                    f"Buys 5m {int(snap.get('buys_5m') or 0)}\n"
                    f"Sells 5m {int(snap.get('sells_5m') or 0)}"
                )
            return

        ok, security_reason = passes_tax_and_sellability(token)
        if not ok:
            send(
                f"⚠️ TRADE SKIPPED\n\n"
                f"Source {source}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Reason: {security_reason}"
            )
            return

        age = snap["age_seconds"] or 0

        should_alert = False
        with LOCK:
            if pair not in ALERTED_POOLS:
                ALERTED_POOLS.add(pair)
                should_alert = True

        if should_alert:
            send(
                f"🚀 NEW LAUNCH DETECTED\n\n"
                f"Source {source}\n"
                f"{snap['symbol']}\n"
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Age {age:.0f}s\n"
                f"Price ${snap['price_usd']:.10f}\n"
                f"Liquidity ${snap['liquidity_usd']:,.0f}\n"
                f"Volume 5m ${snap['volume_5m']:,.0f}\n"
                f"Buys 5m {snap['buys_5m']}\n"
                f"Sells 5m {snap['sells_5m']}\n"
                f"Security: {security_reason}"
            )

        if RUN_PURCHASE == "on":
            if len(LIVE_POSITIONS) >= MAX_OPEN_TRADES:
                send(
                    f"⚠️ LIVE BUY SKIPPED\n\n"
                    f"{snap['symbol']}\n"
                    f"Pair\n{pair}\n\n"
                    f"Reason: max live positions reached\n"
                    f"Live Positions {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}"
                )
                return

            pos = execute_live_buy(token, pair, snap["liquidity_usd"], snap["price_usd"], source)
            if pos:
                LIVE_POSITIONS[token] = pos
                with LOCK:
                    TRADED_POOLS.add(pair)
                threading.Thread(target=monitor_live_position, args=(token,), daemon=True).start()
        else:
            opened = open_paper_trade(token, pair, snap)
            if opened:
                with LOCK:
                    TRADED_POOLS.add(pair)

    finally:
        with LOCK:
            ACTIVE_POOLS.discard(pair)
        if acquired:
            POOL_THREAD_SEMAPHORE.release()


# -------------------------
# EVENT HELPERS
# -------------------------
def parse_v2_event(e) -> Optional[Tuple[str, str, str]]:
    try:
        token0 = e["args"]["token0"]
        token1 = e["args"]["token1"]
        pair = e["args"]["pair"]

        token = None
        if token0.lower() == WETH.lower():
            token = token1
        elif token1.lower() == WETH.lower():
            token = token0

        if token:
            return Web3.to_checksum_address(token), Web3.to_checksum_address(pair), "V2"
    except Exception as ex:
        print("parse_v2_event error:", ex)
    return None


def parse_v3_event(e) -> Optional[Tuple[str, str, str]]:
    try:
        token0 = e["args"]["token0"]
        token1 = e["args"]["token1"]
        pool = e["args"]["pool"]
        fee = int(e["args"]["fee"])

        token = None
        if token0.lower() == WETH.lower():
            token = token1
        elif token1.lower() == WETH.lower():
            token = token0

        if token:
            return Web3.to_checksum_address(token), Web3.to_checksum_address(pool), f"V3:{fee}"
    except Exception as ex:
        print("parse_v3_event error:", ex)
    return None


def process_log_range_with_backoff(contract, event_name: str, start_block: int, end_block: int):
    backoff_seconds = RPC_BACKOFF_START_SECONDS
    while True:
        try:
            if event_name == "PairCreated":
                return contract.events.PairCreated.get_logs(from_block=start_block, to_block=end_block)
            if event_name == "PoolCreated":
                return contract.events.PoolCreated.get_logs(from_block=start_block, to_block=end_block)
            return []
        except Exception as e:
            msg = str(e)
            print(f"{event_name} get_logs error [{start_block}-{end_block}]:", e)

            if "429" in msg or "compute units per second" in msg:
                time.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, RPC_BACKOFF_MAX_SECONDS)
                continue

            if "10 block range" in msg or "up to a 10 block range" in msg:
                time.sleep(2)
                return []

            if "timed out" in msg.lower() or "timeout" in msg.lower():
                time.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, RPC_BACKOFF_MAX_SECONDS)
                continue

            time.sleep(2)
            return []


def dispatch_pool_thread(token: str, pair: str, source: str):
    register_recent_pool(token, pair, source)
    threading.Thread(
        target=process_pool,
        args=(token, pair, source),
        daemon=True,
    ).start()


# -------------------------
# EVENT LISTENERS
# -------------------------
def v2_event_listener():
    last_block = max(safe_block_number() - STARTUP_LOOKBACK_BLOCKS, 0)
    send("Listening for new V2 pairs...")

    while True:
        try:
            block = safe_block_number(last_block)

            if block > last_block:
                start_block = last_block + 1

                while start_block <= block:
                    end_block = min(start_block + MAX_LOG_RANGE - 1, block)
                    events = process_log_range_with_backoff(v2_factory, "PairCreated", start_block, end_block)

                    for e in events:
                        parsed = parse_v2_event(e)
                        if parsed:
                            token, pair, source = parsed
                            dispatch_pool_thread(token, pair, source)

                    start_block = end_block + 1

                last_block = block

            time.sleep(EVENT_POLL_SECONDS)

        except Exception as e:
            print("v2_event_listener outer error:", e)
            time.sleep(5)


def v3_event_listener():
    last_block = max(safe_block_number() - STARTUP_LOOKBACK_BLOCKS, 0)
    send("Listening for new V3 pools...")

    while True:
        try:
            block = safe_block_number(last_block)

            if block > last_block:
                start_block = last_block + 1

                while start_block <= block:
                    end_block = min(start_block + MAX_LOG_RANGE - 1, block)
                    events = process_log_range_with_backoff(v3_factory, "PoolCreated", start_block, end_block)

                    for e in events:
                        parsed = parse_v3_event(e)
                        if parsed:
                            token, pair, source = parsed
                            dispatch_pool_thread(token, pair, source)

                    start_block = end_block + 1

                last_block = block

            time.sleep(EVENT_POLL_SECONDS)

        except Exception as e:
            print("v3_event_listener outer error:", e)
            time.sleep(5)


# -------------------------
# RECENT POOL RESCANNER
# -------------------------
def recent_pool_rescan_loop():
    send("Recent pool rescanner ON")

    while True:
        try:
            cleanup_recent_pools()
            items = get_recent_pool_batch()

            for pair, meta in items:
                token = meta["token"]
                source = meta["source"]
                last_attempt_ts = safe_float(meta.get("last_attempt_ts"), 0.0)

                if now_ts() - last_attempt_ts < RECENT_POOL_RECHECK_COOLDOWN_SECONDS:
                    continue

                with LOCK:
                    if pair in TRADED_POOLS:
                        continue
                    if pair in ACTIVE_POOLS:
                        continue
                    if pair not in RECENT_POOLS:
                        continue
                    RECENT_POOLS[pair]["last_attempt_ts"] = now_ts()

                dispatch_pool_thread(token, pair, source)

            time.sleep(RECENT_POOL_RESCAN_SECONDS)

        except Exception as e:
            print("recent_pool_rescan_loop error:", e)
            time.sleep(5)


# -------------------------
# PORTFOLIO
# -------------------------
def portfolio_loop():
    while True:
        total = ACCOUNT_CASH

        for trade in PAPER_TRADES.values():
            snap = get_pair_snapshot(trade.pair)
            if snap:
                price = safe_float(snap["price_usd"])
                liq = safe_float(snap["liquidity_usd"], default=-1.0)
                update_trade_market_state(trade, price, liq)

            total += get_portfolio_mark_value(trade)

        pnl = total - START_BALANCE
        pnl_pct = (pnl / START_BALANCE) * 100 if START_BALANCE > 0 else 0.0

        send(
            f"📊 PAPER PORTFOLIO\n\n"
            f"Starting Balance ${START_BALANCE:.2f}\n"
            f"Current Value ${total:.2f}\n\n"
            f"Total Profit ${pnl:.2f}\n"
            f"PnL {pnl_pct:.2f}%\n\n"
            f"Cash ${ACCOUNT_CASH:.2f}\n"
            f"Open Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}"
        )

        time.sleep(PORTFOLIO_UPDATE_SECONDS)


def live_account_loop():
    while True:
        if RUN_PURCHASE == "on":
            for token, pos in list(LIVE_POSITIONS.items()):
                try:
                    snap = get_pair_snapshot(pos["pair"])
                    if not snap:
                        continue
                    pos["current_price_usd"] = safe_float(snap.get("price_usd"))
                    pos["current_liquidity_usd"] = safe_float(snap.get("liquidity_usd"))
                    current_price = safe_float(pos.get("current_price_usd"))
                    if current_price > safe_float(pos.get("peak_price"), 0.0):
                        pos["peak_price"] = current_price
                except Exception:
                    pass

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

        time.sleep(LIVE_ACCOUNT_UPDATE_SECONDS)


# -------------------------
# HEARTBEAT
# -------------------------
def heartbeat_loop():
    while True:
        live = get_live_account_snapshot() if RUN_PURCHASE == "on" else None

        extra = ""
        if live is not None:
            extra = (
                f"\nWallet ETH {live['wallet_eth']:.6f}"
                f"\nCash Value ${live['cash_value_usd']:.2f}"
                f"\nOpen Position Value ${live['open_position_value_usd']:.2f}"
                f"\nTotal Live Equity ${live['total_equity_usd']:.2f}"
                f"\nLive Unrealized PnL ${live['unrealized_pnl_usd']:.2f}"
                f"\nLive Unrealized PnL {live['unrealized_pnl_pct']:.2f}%"
            )

        send(
            f"💓 SCANNER HEARTBEAT\n\n"
            f"Connected YES\n"
            f"Block {safe_block_number()}\n"
            f"Mode {'LIVE' if RUN_PURCHASE == 'on' else 'PAPER'}\n"
            f"Paper Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}\n"
            f"Live Positions {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}\n"
            f"Tracked Recent Pools {len(RECENT_POOLS)}\n"
            f"Alerted Pools {len(ALERTED_POOLS)}\n"
            f"Traded Pools {len(TRADED_POOLS)}\n"
            f"Active Pool Threads {len(ACTIVE_POOLS)}\n"
            f"V2 ON\n"
            f"V3 ON\n"
            f"Recent Rescan ON"
            f"{extra}"
        )
        time.sleep(HEARTBEAT_SECONDS)


# -------------------------
# MAIN
# -------------------------
def main():
    send(
        f"Launch Sniper Started\n\n"
        f"Mode {'LIVE' if RUN_PURCHASE == 'on' else 'PAPER'}\n"
        f"Balance ${START_BALANCE:.2f}\n"
        f"Buy Size ${PURCHASE_AMOUNT_USD:.2f}\n"
        f"Max Open Trades {MAX_OPEN_TRADES}\n"
        f"Age Limit {MAX_TOKEN_AGE_SECONDS}s\n"
        f"Min Liquidity ${MIN_LIQUIDITY_USD:,.0f}\n"
        f"Min Volume 5m ${MIN_VOLUME_5M_USD:,.0f}\n"
        f"Entry Rules: volume there and sells >= {MIN_SELLS_5M}\n"
        f"Security Rules: sell tax <= {MAX_SELL_TAX_PCT:.2f}% | buy tax <= {MAX_BUY_TAX_PCT:.2f}%\n"
        f"Exit Rules: trail arm {TRAIL_ARM_PCT:.0f}% | trail drop {TRAIL_DROP_PCT:.0f}% | liquidity drop {LIQUIDITY_DROP_EXIT_PCT:.0f}%\n"
        f"Paper Exit Model: haircut {PAPER_EXIT_HAIRCUT_PCT:.0f}% | cap {PAPER_EXIT_MAX_BUYSIDE_SHARE * 100:.0f}% buy-side\n"
        f"Discovery: V2 + V3 + Recent Pool Rescan\n"
        f"Startup Lookback {STARTUP_LOOKBACK_BLOCKS} blocks\n"
        f"Recent Pool Rescan Every {RECENT_POOL_RESCAN_SECONDS}s\n"
        f"Live Account Updates Every {LIVE_ACCOUNT_UPDATE_SECONDS}s"
    )

    threading.Thread(target=v2_event_listener, daemon=True).start()
    threading.Thread(target=v3_event_listener, daemon=True).start()
    threading.Thread(target=recent_pool_rescan_loop, daemon=True).start()
    threading.Thread(target=portfolio_loop, daemon=True).start()
    threading.Thread(target=live_account_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
