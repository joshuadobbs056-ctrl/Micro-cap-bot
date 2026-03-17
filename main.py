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
PORTFOLIO_UPDATE_SECONDS = int(os.getenv("PORTFOLIO_UPDATE_SECONDS", "300"))

LOOKBACK_POINTS = int(os.getenv("LOOKBACK_POINTS", "6"))  # with 30s checks, 6 = ~3 minutes
ENTRY_PUMP_PCT = float(os.getenv("ENTRY_PUMP_PCT", "2.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "5.0"))

TRAIL_ARM_PCT = float(os.getenv("TRAIL_ARM_PCT", "5.0"))
TRAIL_DROP_PCT = float(os.getenv("TRAIL_DROP_PCT", "2.0"))

MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "100000"))
MIN_24H_VOLUME_USD = float(os.getenv("MIN_24H_VOLUME_USD", "1000000"))
MIN_PRICE_USD = float(os.getenv("MIN_PRICE_USD", "0.0000001"))

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

V3_DEFAULT_FEE = int(os.getenv("V3_DEFAULT_FEE", "3000"))
V3_FEE_CANDIDATES = [500, 3000, 10000]

DEX_PREFERRED_CHAIN = os.getenv("DEX_PREFERRED_CHAIN", "ethereum").strip().lower()
PAIR_STALE_SECONDS = int(os.getenv("PAIR_STALE_SECONDS", "180"))

# comma separated symbol filter if you want to narrow down further
WATCH_SYMBOLS_FILTER = {
    s.strip().upper()
    for s in os.getenv("WATCH_SYMBOLS_FILTER", "").split(",")
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
# TRUSTED ETHEREUM TOKEN UNIVERSE
# -------------------------
TRUSTED_TOKENS = {
    "ETH": {"symbol": "ETH", "token": "0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2", "name": "Wrapped Ether"},
    "WBTC": {"symbol": "WBTC", "token": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "name": "Wrapped BTC"},
    "LINK": {"symbol": "LINK", "token": "0x514910771AF9Ca656af840dff83E8264EcF986CA", "name": "Chainlink"},
    "UNI": {"symbol": "UNI", "token": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "name": "Uniswap"},
    "AAVE": {"symbol": "AAVE", "token": "0x7Fc66500c84A76Ad7E9c93437bFc5Ac33E2DDAE9", "name": "Aave"},
    "MKR": {"symbol": "MKR", "token": "0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2", "name": "Maker"},
    "CRV": {"symbol": "CRV", "token": "0xD533a949740bb3306d119CC777fa900bA034cd52", "name": "Curve DAO"},
    "SNX": {"symbol": "SNX", "token": "0xC011A72400E58ecD99Ee497CF89E3775d4bd732F", "name": "Synthetix"},
    "COMP": {"symbol": "COMP", "token": "0xc00e94Cb662C3520282E6f5717214004A7f26888", "name": "Compound"},
    "LDO": {"symbol": "LDO", "token": "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32", "name": "Lido DAO"},
    "RPL": {"symbol": "RPL", "token": "0xD33526068D116cE69F19A9ee46F0bd304F21A51f", "name": "Rocket Pool"},
    "PENDLE": {"symbol": "PENDLE", "token": "0x808507121B80c02388fAd14726482e061B8da827", "name": "Pendle"},
    "ONDO": {"symbol": "ONDO", "token": "0xFAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3", "name": "Ondo"},
    "MNT": {"symbol": "MNT", "token": "0x3c3a81e81dc49A522A592e7622A7E711c06bf354", "name": "Mantle"},
    "AXS": {"symbol": "AXS", "token": "0xBB0E17EF65F82Ab018d8EDd776e8Dd940327B28b", "name": "Axie Infinity"},
    "SUSHI": {"symbol": "SUSHI", "token": "0x6B3595068778DD592e39A122f4f5a5Cf09C90fE2", "name": "Sushi"},
    "BAL": {"symbol": "BAL", "token": "0xba100000625a3754423978a60c9317c58a424e3D", "name": "Balancer"},
    "1INCH": {"symbol": "1INCH", "token": "0x111111111117dC0aa78b770fA6A738034120C302", "name": "1inch"},
    "YFI": {"symbol": "YFI", "token": "0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e", "name": "yearn.finance"},
    "REN": {"symbol": "REN", "token": "0x408e41876cCCDC0F92210600ef50372656052a38", "name": "Ren"},
    "BAT": {"symbol": "BAT", "token": "0x0D8775F648430679A709E98d2b0Cb6250d2887EF", "name": "Basic Attention Token"},
    "ZRX": {"symbol": "ZRX", "token": "0xE41d2489571d322189246DaFA5ebDe1F4699F498", "name": "0x"},
    "MANA": {"symbol": "MANA", "token": "0x0F5D2fB29fb7d3CFeE444a200298f468908cC942", "name": "Decentraland"},
    "ENJ": {"symbol": "ENJ", "token": "0xF629cBd94d3791C9250152BD8dFbDF380E2a3B9c", "name": "Enjin"},
    "APE": {"symbol": "APE", "token": "0x4d224452801ACEd8B2F0aebE155379bb5D594381", "name": "ApeCoin"},
    "PEPE": {"symbol": "PEPE", "token": "0x6982508145454Ce325dDbE47a25d4ec3d2311933", "name": "Pepe"},
    "SHIB": {"symbol": "SHIB", "token": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", "name": "Shiba Inu"},
    "FLOKI": {"symbol": "FLOKI", "token": "0xcf0c122c6b73ff809c693db761e7b5b7f5b3c6c0", "name": "Floki"},
}

if WATCH_SYMBOLS_FILTER:
    TRUSTED_TOKENS = {
        sym: data for sym, data in TRUSTED_TOKENS.items()
        if sym in WATCH_SYMBOLS_FILTER
    }


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
MARKET_CACHE: Dict[str, dict] = {}
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
# DEXSCREENER
# -------------------------
def dexscreener_get_token_pairs(token: str) -> Optional[dict]:
    try:
        r = SESSION.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{Web3.to_checksum_address(token)}",
            timeout=20,
        )
        if r.status_code != 200:
            print(f"DexScreener error {r.status_code} for token {token}: {r.text[:200]}")
            return None
        body = (r.text or "").strip()
        if not body:
            return None
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"DexScreener request failed for token {token}: {e}")
        return None


def get_best_pair_snapshot(token: str) -> Optional[dict]:
    data = dexscreener_get_token_pairs(token)
    if not data:
        return None

    pairs = data.get("pairs") or []
    best = None
    best_score = -1.0

    for p in pairs:
        if str(p.get("chainId") or "").lower() != DEX_PREFERRED_CHAIN:
            continue

        liquidity_usd = safe_float((p.get("liquidity") or {}).get("usd"))
        vol24 = safe_float((p.get("volume") or {}).get("h24"))
        price_usd = safe_float(p.get("priceUsd"))
        if liquidity_usd < MIN_LIQUIDITY_USD:
            continue
        if vol24 < MIN_24H_VOLUME_USD:
            continue
        if price_usd < MIN_PRICE_USD:
            continue

        score = liquidity_usd + (vol24 * 0.25)
        if score > best_score:
            txns_h1 = (p.get("txns") or {}).get("h1") or {}
            txns_m5 = (p.get("txns") or {}).get("m5") or {}
            best = {
                "pair_address": p.get("pairAddress"),
                "dex_id": p.get("dexId"),
                "url": p.get("url"),
                "price_usd": price_usd,
                "liquidity_usd": liquidity_usd,
                "volume_h24_usd": vol24,
                "volume_m5_usd": safe_float((p.get("volume") or {}).get("m5")),
                "buys_h1": int(txns_h1.get("buys") or 0),
                "sells_h1": int(txns_h1.get("sells") or 0),
                "buys_m5": int(txns_m5.get("buys") or 0),
                "sells_m5": int(txns_m5.get("sells") or 0),
                "pair_created_at_ms": p.get("pairCreatedAt"),
                "source": "dexscreener",
                "updated_ts": now_ts(),
            }
            best_score = score

    return best


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

    ok, expected_out, amount_out_min, _ = get_v2_quote(amount_in_wei, token)
    if ok:
        best = ("V2", 0, expected_out, amount_out_min, "ok")

    for fee in V3_FEE_CANDIDATES:
        ok3, expected_out3, amount_out_min3, _ = get_v3_quote(amount_in_wei, token, fee)
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


def execute_live_buy(token: str, symbol: str, entry_price_usd: float) -> Optional[dict]:
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
def open_paper_position(symbol: str, token: str, price: float) -> bool:
    global ACCOUNT_CASH

    if len(PAPER_POSITIONS) >= MAX_OPEN_TRADES:
        return False
    if ACCOUNT_CASH < BUY_SIZE_USD:
        return False
    if price <= 0:
        return False

    qty = BUY_SIZE_USD / price
    PAPER_POSITIONS[token] = {
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
            with LOCK:
                cache_copy = dict(MARKET_CACHE)

            for token, pos in list(PAPER_POSITIONS.items()):
                market = cache_copy.get(token)
                if not market:
                    continue

                current_price = safe_float(market.get("price_usd"))
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
                        PAPER_POSITIONS.pop(token, None)
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
                    PAPER_POSITIONS.pop(token, None)

        except Exception as e:
            print(f"monitor_paper_positions error: {e}")

        time.sleep(POSITION_CHECK_SECONDS)


# -------------------------
# LIVE POSITION MONITOR
# -------------------------
def monitor_live_positions():
    while True:
        try:
            with LOCK:
                cache_copy = dict(MARKET_CACHE)

            for token, pos in list(LIVE_POSITIONS.items()):
                market = cache_copy.get(token)
                if not market:
                    continue

                current_price = safe_float(market.get("price_usd"))
                if current_price <= 0:
                    continue

                entry_price = safe_float(pos.get("entry_price"))
                pos["current_price_usd"] = current_price

                if current_price > safe_float(pos.get("peak_price"), 0.0):
                    pos["peak_price"] = current_price

                peak_price = safe_float(pos.get("peak_price"))
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
def update_price_history(symbol: str, token: str, market: dict):
    price = safe_float(market.get("price_usd"))
    if price <= 0:
        return

    with LOCK:
        if token not in PRICE_HISTORY:
            PRICE_HISTORY[token] = deque(maxlen=max(LOOKBACK_POINTS, 2))
        PRICE_HISTORY[token].append((now_ts(), price))
        MARKET_CACHE[token] = market


def get_signal_for_token(symbol: str, token: str, market: dict) -> Tuple[bool, str]:
    price = safe_float(market.get("price_usd"))
    liquidity = safe_float(market.get("liquidity_usd"))
    vol24 = safe_float(market.get("volume_h24_usd"))

    if price <= 0:
        return False, "bad price"
    if liquidity < MIN_LIQUIDITY_USD:
        return False, f"liquidity too low {liquidity:.0f}"
    if vol24 < MIN_24H_VOLUME_USD:
        return False, f"24h vol too low {vol24:.0f}"

    with LOCK:
        hist = list(PRICE_HISTORY.get(token, []))
        last_signal = LAST_SIGNAL_TS.get(token, 0.0)

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

    return True, f"momentum breakout {move_pct:.2f}% over {len(hist)} points"


def process_signal(symbol: str, token: str, market: dict):
    price = safe_float(market.get("price_usd"))
    if price <= 0:
        return

    signal_ok, signal_reason = get_signal_for_token(symbol, token, market)
    if not signal_ok:
        return

    with LOCK:
        LAST_SIGNAL_TS[token] = now_ts()

    if RUN_AUTO_BUY == "on":
        if len(LIVE_POSITIONS) >= MAX_OPEN_TRADES:
            return
        if token in LIVE_POSITIONS:
            return

        pos = execute_live_buy(token, symbol, price)
        if pos:
            LIVE_POSITIONS[token] = pos
    else:
        if token not in PAPER_POSITIONS:
            open_paper_position(symbol, token, price)

    send(
        f"🚀 SIGNAL DETECTED\n\n"
        f"{symbol}\n"
        f"Token\n{token}\n\n"
        f"Price ${price:.8f}\n"
        f"Liquidity ${safe_float(market.get('liquidity_usd')):,.0f}\n"
        f"24h Volume ${safe_float(market.get('volume_h24_usd')):,.0f}\n"
        f"Reason: {signal_reason}"
    )


def scanner_loop():
    send("Trusted-coin DexScreener scanner ON")

    while True:
        try:
            for symbol, meta in TRUSTED_TOKENS.items():
                token = Web3.to_checksum_address(meta["token"])
                snap = get_best_pair_snapshot(token)
                if not snap:
                    continue

                update_price_history(symbol, token, snap)
                process_signal(symbol, token, snap)

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
                f"Trusted Coins {len(TRUSTED_TOKENS)}\n"
                f"Tracked Histories {len(PRICE_HISTORY)}\n"
                f"Paper Trades {len(PAPER_POSITIONS)}/{MAX_OPEN_TRADES}\n"
                f"Live Positions {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}\n"
                f"Check Every {CHECK_INTERVAL_SECONDS}s\n"
                f"Buy Size ${BUY_SIZE_USD:.2f}\n"
                f"Entry Pump {ENTRY_PUMP_PCT:.2f}%\n"
                f"Trail Arm {TRAIL_ARM_PCT:.2f}%\n"
                f"Trail Drop {TRAIL_DROP_PCT:.2f}%\n"
                f"Stop Loss {STOP_LOSS_PCT:.2f}%\n"
                f"Min Liquidity ${MIN_LIQUIDITY_USD:,.0f}\n"
                f"Min 24h Volume ${MIN_24H_VOLUME_USD:,.0f}"
                f"{extra}"
            )
        except Exception as e:
            print(f"heartbeat_loop error: {e}")

        time.sleep(HEARTBEAT_SECONDS)


# -------------------------
# MAIN
# -------------------------
def main():
    watched_symbols = ",".join(sorted(TRUSTED_TOKENS.keys()))

    send(
        f"Trusted Coin Momentum Bot Started\n\n"
        f"Mode {'LIVE' if RUN_AUTO_BUY == 'on' else 'PAPER'}\n"
        f"Buy Size ${BUY_SIZE_USD:.2f}\n"
        f"Max Open Trades {MAX_OPEN_TRADES}\n"
        f"Check Interval {CHECK_INTERVAL_SECONDS}s\n"
        f"Lookback Points {LOOKBACK_POINTS}\n"
        f"Entry Pump {ENTRY_PUMP_PCT:.2f}%\n"
        f"Trail Arm {TRAIL_ARM_PCT:.2f}%\n"
        f"Trail Drop {TRAIL_DROP_PCT:.2f}%\n"
        f"Stop Loss {STOP_LOSS_PCT:.2f}%\n"
        f"Min Liquidity ${MIN_LIQUIDITY_USD:,.0f}\n"
        f"Min 24h Volume ${MIN_24H_VOLUME_USD:,.0f}\n"
        f"Slippage {SLIPPAGE_BPS} bps\n"
        f"Trusted Symbols {watched_symbols}"
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
