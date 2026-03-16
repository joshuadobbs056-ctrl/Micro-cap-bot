import os
import sys
import time
import threading
import subprocess
from typing import Optional, Dict, Any, Tuple


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
DISCOVERY_WAIT_SECONDS = int(os.getenv("DISCOVERY_WAIT_SECONDS", "10"))
DISCOVERY_POLL_SECONDS = float(os.getenv("DISCOVERY_POLL_SECONDS", "3"))
POSITION_CHECK_SECONDS = int(os.getenv("POSITION_CHECK_SECONDS", "20"))

# scan recent blocks on startup so the scanner can prove detection quickly
STARTUP_LOOKBACK_BLOCKS = int(os.getenv("STARTUP_LOOKBACK_BLOCKS", "250"))

# earliest launch only
MAX_TOKEN_AGE_SECONDS = int(os.getenv("MAX_TOKEN_AGE_SECONDS", "900"))  # 15 min

# entry criteria
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "20000"))
MIN_VOLUME_5M_USD = float(os.getenv("MIN_VOLUME_5M_USD", "1000"))
MIN_SELLS_5M = int(os.getenv("MIN_SELLS_5M", "0"))
MIN_BUYS_5M = int(os.getenv("MIN_BUYS_5M", "1"))

# tax / sellability filter
MAX_SELL_TAX_PCT = float(os.getenv("MAX_SELL_TAX_PCT", "10"))
MAX_BUY_TAX_PCT = float(os.getenv("MAX_BUY_TAX_PCT", "15"))

# exit rules
LIQUIDITY_DROP_EXIT_PCT = float(os.getenv("LIQUIDITY_DROP_EXIT_PCT", "25"))

# trailing exit
TRAIL_ARM_PCT = float(os.getenv("TRAIL_ARM_PCT", "20"))
TRAIL_DROP_PCT = float(os.getenv("TRAIL_DROP_PCT", "15"))

# paper exit simulation
PAPER_EXIT_HAIRCUT_PCT = float(os.getenv("PAPER_EXIT_HAIRCUT_PCT", "30"))
PAPER_EXIT_MAX_BUYSIDE_SHARE = float(os.getenv("PAPER_EXIT_MAX_BUYSIDE_SHARE", "0.10"))
PAPER_EXIT_MIN_VALUE_USD = float(os.getenv("PAPER_EXIT_MIN_VALUE_USD", "0.01"))
PAPER_MARK_PRICE_FALLBACK_HAIRCUT_PCT = float(os.getenv("PAPER_MARK_PRICE_FALLBACK_HAIRCUT_PCT", "15"))
PAPER_STALE_SNAPSHOT_SECONDS = int(os.getenv("PAPER_STALE_SNAPSHOT_SECONDS", "90"))
PAPER_STALE_MARKDOWN_PCT = float(os.getenv("PAPER_STALE_MARKDOWN_PCT", "35"))

# live buy settings
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "1500"))
GAS_LIMIT_BUY = int(os.getenv("GAS_LIMIT_BUY", "450000"))
GAS_LIMIT_APPROVE = int(os.getenv("GAS_LIMIT_APPROVE", "120000"))
GAS_LIMIT_SELL = int(os.getenv("GAS_LIMIT_SELL", "450000"))
BUY_DEADLINE_SECONDS = int(os.getenv("BUY_DEADLINE_SECONDS", "180"))
SELL_DEADLINE_SECONDS = int(os.getenv("SELL_DEADLINE_SECONDS", "180"))

# provider limits / retry
MAX_LOG_RANGE = int(os.getenv("MAX_LOG_RANGE", "10"))
RPC_BACKOFF_START_SECONDS = int(os.getenv("RPC_BACKOFF_START_SECONDS", "2"))
RPC_BACKOFF_MAX_SECONDS = int(os.getenv("RPC_BACKOFF_MAX_SECONDS", "30"))

# optional hard cap so listener threads do not get bogged down
MAX_ACTIVE_POOL_THREADS = int(os.getenv("MAX_ACTIVE_POOL_THREADS", "100"))


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

CHAINLINK_ETH_USD_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

v2_factory = w3.eth.contract(address=V2_FACTORY, abi=V2_FACTORY_ABI)
v3_factory = w3.eth.contract(address=V3_FACTORY, abi=V3_FACTORY_ABI)
router = w3.eth.contract(address=ROUTER, abi=ROUTER_ABI)
eth_usd_feed = w3.eth.contract(address=CHAINLINK_ETH_USD, abi=CHAINLINK_ETH_USD_ABI)


# -------------------------
# TELEGRAM
# -------------------------
def send(msg: str):
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            requests.post(
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
    return {
        "from": wallet_address,
        "value": value,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
    }


# -------------------------
# SECURITY / TAX CHECK
# -------------------------
def get_token_security(token: str) -> Optional[dict]:
    try:
        url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
        r = requests.get(url, timeout=10)
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
        r = requests.get(url, timeout=10)
        data = r.json()

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
    except Exception as e:
        print("get_pair_snapshot error:", e)
        return None


# -------------------------
# LIVE BUY/SELL
# -------------------------
def approve_token_if_needed(token: str, amount_raw: int) -> bool:
    if not ACCOUNT:
        return False

    token_contract = get_token_contract(token)
    wallet = ACCOUNT.address

    try:
        allowance = int(token_contract.functions.allowance(wallet, ROUTER).call())
        if allowance >= amount_raw:
            return True

        nonce = w3.eth.get_transaction_count(wallet, "pending")
        tx = token_contract.functions.approve(
            ROUTER,
            2**256 - 1,
        ).build_transaction(build_tx_params(wallet, nonce, GAS_LIMIT_APPROVE))

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        return receipt.status == 1
    except Exception as e:
        send(f"❌ APPROVE ERROR\n{token}\n{e}")
        return False


def execute_live_buy(token: str, pair: str, entry_liquidity_usd: float, entry_price: float) -> Optional[dict]:
    if not ACCOUNT:
        send("⚠️ LIVE BUY SKIPPED\nReason: PRIVATE_KEY not loaded")
        return None

    wallet = ACCOUNT.address
    token = Web3.to_checksum_address(token)
    _, symbol, decimals = get_token_meta(token)

    try:
        eth_amount = usd_to_eth(PURCHASE_AMOUNT_USD)
        value_wei = int(w3.to_wei(eth_amount, "ether"))
        path = [WETH, token]

        token_contract = get_token_contract(token)
        balance_before = int(token_contract.functions.balanceOf(wallet).call())

        try:
            amounts_out = router.functions.getAmountsOut(value_wei, path).call()
            expected_out = int(amounts_out[-1])
            amount_out_min = int(expected_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception:
            amount_out_min = 0

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
            send(f"❌ LIVE BUY FAILED\n{symbol}\n{token}")
            return None

        balance_after = int(token_contract.functions.balanceOf(wallet).call())
        token_amount_raw = max(balance_after - balance_before, 0)
        token_amount = token_amount_raw / (10 ** decimals)

        send(
            f"🟢 LIVE BUY OPENED\n\n"
            f"{symbol}\n"
            f"Token\n{token}\n\n"
            f"Pair\n{pair}\n\n"
            f"Entry Price ${entry_price:.10f}\n"
            f"Buy Size ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH {eth_amount:.6f}\n"
            f"Tokens {token_amount:,.6f}"
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
        }

    except Exception as e:
        send(f"❌ LIVE BUY ERROR\n{token}\n{e}")
        return None


def execute_live_sell(position: dict, current_price_usd: float, current_liquidity_usd: float, reason: str) -> bool:
    if not ACCOUNT:
        return False

    token = position["token"]
    pair = position["pair"]
    symbol = position["symbol"]
    amount_raw = position["token_amount_raw"]
    wallet = ACCOUNT.address

    try:
        if amount_raw <= 0:
            return False
        if not approve_token_if_needed(token, amount_raw):
            return False

        path = [token, WETH]
        try:
            amounts_out = router.functions.getAmountsOut(amount_raw, path).call()
            expected_eth_out = int(amounts_out[-1])
            amount_out_min = int(expected_eth_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception:
            amount_out_min = 0

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
                f"Current Price ${current_price_usd:.10f}\n"
                f"Current Liquidity ${current_liquidity_usd:,.0f}\n"
                f"Reason: {reason}"
            )
            return True

        send(f"❌ LIVE SELL FAILED\n{symbol}\n{token}")
        return False

    except Exception as e:
        send(f"❌ LIVE SELL ERROR\n{symbol}\n{token}\n{e}")
        return False


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
SEEN_POOLS = set()
ACTIVE_POOLS = set()
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

        if current_price > pos.get("peak_price", 0.0):
            pos["peak_price"] = current_price

        peak_price = safe_float(pos.get("peak_price"))

        send(
            f"📊 LIVE POSITION UPDATE\n\n"
            f"{pos['symbol']}\n"
            f"Token\n{pos['token']}\n\n"
            f"Entry Price ${entry_price:.10f}\n"
            f"Current Price ${current_price:.10f}\n"
            f"Peak Price ${peak_price:.10f}\n"
            f"Entry Liquidity ${pos['entry_liquidity_usd']:,.0f}\n"
            f"Current Liquidity ${current_liq:,.0f}"
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
def open_paper_trade(token: str, pair: str, snap: dict):
    global ACCOUNT_CASH

    if len(PAPER_TRADES) >= MAX_OPEN_TRADES:
        send(f"⚠️ TRADE SKIPPED\nReason: max open trades reached\nOpen Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}")
        return

    if ACCOUNT_CASH < PURCHASE_AMOUNT_USD:
        send(f"⚠️ TRADE SKIPPED\nReason: insufficient balance\nCash ${ACCOUNT_CASH:.2f}\nRequired ${PURCHASE_AMOUNT_USD:.2f}")
        return

    symbol = snap["symbol"] or "UNK"
    price = snap["price_usd"]
    liq = snap["liquidity_usd"]

    if price <= 0:
        send(f"⚠️ TRADE SKIPPED\n{symbol}\nReason: no entry price")
        return

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


# -------------------------
# PROCESS NEW POOL
# -------------------------
def qualifies_for_entry(snap: dict) -> bool:
    age = snap["age_seconds"]
    if age is None or age > MAX_TOKEN_AGE_SECONDS:
        return False
    if snap["liquidity_usd"] < MIN_LIQUIDITY_USD:
        return False
    if snap["volume_5m"] < MIN_VOLUME_5M_USD:
        return False
    if snap["sells_5m"] < MIN_SELLS_5M:
        return False
    if snap["buys_5m"] < MIN_BUYS_5M:
        return False
    if snap["price_usd"] <= 0:
        return False
    return True


def process_pool(token: str, pair: str, source: str):
    acquired = False
    try:
        acquired = POOL_THREAD_SEMAPHORE.acquire(timeout=1)
        if not acquired:
            return

        with LOCK:
            if pair in ACTIVE_POOLS or pair in SEEN_POOLS:
                return
            ACTIVE_POOLS.add(pair)

        snap = None
        started = now_ts()

        while now_ts() - started < DISCOVERY_WAIT_SECONDS:
            snap = get_pair_snapshot(pair)
            if snap and qualifies_for_entry(snap):
                break
            time.sleep(DISCOVERY_POLL_SECONDS)

        if not snap or not qualifies_for_entry(snap):
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

        with LOCK:
            SEEN_POOLS.add(pair)

        if RUN_PURCHASE == "on":
            if len(LIVE_POSITIONS) >= MAX_OPEN_TRADES:
                send(f"⚠️ LIVE TRADE SKIPPED\nReason: max open trades reached\nOpen Trades {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}")
                return
            pos = execute_live_buy(token, pair, snap["liquidity_usd"], snap["price_usd"])
            if pos:
                LIVE_POSITIONS[token] = pos
                threading.Thread(target=monitor_live_position, args=(token,), daemon=True).start()
        else:
            open_paper_trade(token, pair, snap)

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

        token = None
        if token0.lower() == WETH.lower():
            token = token1
        elif token1.lower() == WETH.lower():
            token = token0

        if token:
            return Web3.to_checksum_address(token), Web3.to_checksum_address(pool), "V3"
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


# -------------------------
# HEARTBEAT
# -------------------------
def heartbeat_loop():
    while True:
        send(
            f"💓 SCANNER HEARTBEAT\n\n"
            f"Connected YES\n"
            f"Block {safe_block_number()}\n"
            f"Mode {'LIVE' if RUN_PURCHASE == 'on' else 'PAPER'}\n"
            f"Paper Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}\n"
            f"Live Positions {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}\n"
            f"Seen Pools {len(SEEN_POOLS)}\n"
            f"Active Pool Threads {len(ACTIVE_POOLS)}\n"
            f"V2 ON\n"
            f"V3 ON"
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
        f"Entry Rules: volume there and sells >= {MIN_SELLS_5M}\n"
        f"Security Rules: sell tax <= {MAX_SELL_TAX_PCT:.2f}% | buy tax <= {MAX_BUY_TAX_PCT:.2f}%\n"
        f"Exit Rules: trail arm {TRAIL_ARM_PCT:.0f}% | trail drop {TRAIL_DROP_PCT:.0f}% | liquidity drop {LIQUIDITY_DROP_EXIT_PCT:.0f}%\n"
        f"Paper Exit Model: haircut {PAPER_EXIT_HAIRCUT_PCT:.0f}% | cap {PAPER_EXIT_MAX_BUYSIDE_SHARE * 100:.0f}% buy-side\n"
        f"Discovery: V2 + V3\n"
        f"Startup Lookback {STARTUP_LOOKBACK_BLOCKS} blocks"
    )

    threading.Thread(target=v2_event_listener, daemon=True).start()
    threading.Thread(target=v3_event_listener, daemon=True).start()
    threading.Thread(target=portfolio_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
