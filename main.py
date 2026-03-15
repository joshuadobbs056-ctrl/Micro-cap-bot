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

PORTFOLIO_UPDATE_SECONDS = int(os.getenv("PORTFOLIO_UPDATE_SECONDS", "30"))
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "300"))
EVENT_POLL_SECONDS = float(os.getenv("EVENT_POLL_SECONDS", "1"))
DISCOVERY_WAIT_SECONDS = int(os.getenv("DISCOVERY_WAIT_SECONDS", "20"))
DISCOVERY_POLL_SECONDS = float(os.getenv("DISCOVERY_POLL_SECONDS", "2"))
POSITION_CHECK_SECONDS = int(os.getenv("POSITION_CHECK_SECONDS", "15"))

# earliest launch only
MAX_TOKEN_AGE_SECONDS = int(os.getenv("MAX_TOKEN_AGE_SECONDS", "300"))  # 5 min

# entry criteria
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "15000"))
MIN_VOLUME_5M_USD = float(os.getenv("MIN_VOLUME_5M_USD", "5000"))
MIN_SELLS_5M = int(os.getenv("MIN_SELLS_5M", "1"))
MIN_BUYS_5M = int(os.getenv("MIN_BUYS_5M", "2"))

# tax / sellability filter
MAX_SELL_TAX_PCT = float(os.getenv("MAX_SELL_TAX_PCT", "10"))
MAX_BUY_TAX_PCT = float(os.getenv("MAX_BUY_TAX_PCT", "15"))

# exit criteria - ONLY liquidity drop
LIQUIDITY_DROP_EXIT_PCT = float(os.getenv("LIQUIDITY_DROP_EXIT_PCT", "25"))

# live buy settings
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "1500"))
GAS_LIMIT_BUY = int(os.getenv("GAS_LIMIT_BUY", "450000"))
GAS_LIMIT_APPROVE = int(os.getenv("GAS_LIMIT_APPROVE", "120000"))
GAS_LIMIT_SELL = int(os.getenv("GAS_LIMIT_SELL", "450000"))
BUY_DEADLINE_SECONDS = int(os.getenv("BUY_DEADLINE_SECONDS", "180"))
SELL_DEADLINE_SECONDS = int(os.getenv("SELL_DEADLINE_SECONDS", "180"))

# -------------------------
# WEB3
# -------------------------
if not NODE:
    raise RuntimeError("NODE missing")

if NODE.startswith("ws"):
    w3 = Web3(Web3.LegacyWebSocketProvider(NODE))
else:
    w3 = Web3(Web3.HTTPProvider(NODE))

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
FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
ROUTER = Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
CHAINLINK_ETH_USD = Web3.to_checksum_address("0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419")

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
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

factory = w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)
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
    data = eth_usd_feed.functions.latestRoundData().call()
    return int(data[1]) / 10**8

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
        return False, "security check unavailable"

    sell_tax = safe_pct(sec.get("sell_tax"))
    buy_tax = safe_pct(sec.get("buy_tax"))

    if sec.get("is_honeypot") == "1":
        return False, "honeypot flagged"

    if sec.get("cannot_sell_all") == "1":
        return False, "cannot sell all flagged"

    if sell_tax > MAX_SELL_TAX_PCT:
        return False, f"sell tax too high: {sell_tax:.2f}%"

    if buy_tax > MAX_BUY_TAX_PCT:
        return False, f"buy tax too high: {buy_tax:.2f}%"

    return True, f"buy tax {buy_tax:.2f}% | sell tax {sell_tax:.2f}%"

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
            return None
        created_ms = p.get("pairCreatedAt")
        age_seconds = None
        if created_ms:
            age_seconds = (now_ts() * 1000 - float(created_ms)) / 1000.0
        return {
            "pair": p.get("pairAddress"),
            "token": ((p.get("baseToken") or {}).get("address") or ""),
            "symbol": ((p.get("baseToken") or {}).get("symbol") or "UNK"),
            "price_usd": safe_float(p.get("priceUsd")),
            "liquidity_usd": safe_float((p.get("liquidity") or {}).get("usd")),
            "volume_5m": safe_float((p.get("volume") or {}).get("m5")),
            "buys_5m": int(((p.get("txns") or {}).get("m5") or {}).get("buys") or 0),
            "sells_5m": int(((p.get("txns") or {}).get("m5") or {}).get("sells") or 0),
            "fdv": safe_float(p.get("fdv")),
            "age_seconds": age_seconds,
            "url": p.get("url", ""),
        }
    except Exception:
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

def execute_live_buy(token: str, pair: str, entry_liquidity_usd: float) -> Optional[dict]:
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
            f"Buy Size ${PURCHASE_AMOUNT_USD:.2f}\n"
            f"Approx ETH {eth_amount:.6f}\n"
            f"Tokens {token_amount:,.6f}"
        )

        return {
            "token": token,
            "pair": pair,
            "symbol": symbol,
            "entry_price": 0.0,
            "entry_liquidity_usd": entry_liquidity_usd,
            "token_amount_raw": token_amount_raw,
            "decimals": decimals,
            "opened": now_ts(),
        }

    except Exception as e:
        send(f"❌ LIVE BUY ERROR\n{token}\n{e}")
        return None

def execute_live_sell(position: dict, current_price_usd: float, current_liquidity_usd: float):
    if not ACCOUNT:
        return
    token = position["token"]
    pair = position["pair"]
    symbol = position["symbol"]
    amount_raw = position["token_amount_raw"]
    wallet = ACCOUNT.address

    try:
        if amount_raw <= 0:
            return
        if not approve_token_if_needed(token, amount_raw):
            return

        path = [token, WETH]
        try:
            amounts_out = router.functions.getAmountsOut(amount_raw, path).call()
            expected_eth_out = int(amounts_out[-1])
            amount_out_min = int(expected_eth_out * (10000 - SLIPPAGE_BPS) / 10000)
        except Exception:
            expected_eth_out = 0
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
                f"Reason: liquidity dropped {LIQUIDITY_DROP_EXIT_PCT:.0f}%"
            )
        else:
            send(f"❌ LIVE SELL FAILED\n{symbol}\n{token}")
    except Exception as e:
        send(f"❌ LIVE SELL ERROR\n{symbol}\n{token}\n{e}")

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

PAPER_TRADES: Dict[str, PaperTrade] = {}
LIVE_POSITIONS: Dict[str, dict] = {}
SEEN_PAIRS = set()
ACTIVE = set()
LOCK = threading.Lock()

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
        if not snap:
            continue

        price = snap["price_usd"]
        current_liq = snap["liquidity_usd"]
        value = trade.tokens * price if price > 0 else 0.0
        pnl = ((value - PURCHASE_AMOUNT_USD) / PURCHASE_AMOUNT_USD) * 100 if PURCHASE_AMOUNT_USD > 0 else 0.0

        send(
            f"📊 PAPER TRADE UPDATE\n\n"
            f"{trade.symbol}\n"
            f"Token\n{trade.token}\n\n"
            f"Entry ${trade.entry_price:.10f}\n"
            f"Current ${price:.10f}\n\n"
            f"Entry Liquidity ${trade.entry_liquidity_usd:,.0f}\n"
            f"Current Liquidity ${current_liq:,.0f}\n\n"
            f"Value ${value:.2f}\n"
            f"PnL {pnl:.2f}%"
        )

        exit_floor = trade.entry_liquidity_usd * (1 - LIQUIDITY_DROP_EXIT_PCT / 100.0)
        if current_liq <= exit_floor:
            ACCOUNT_CASH += value
            send(
                f"🧪 PAPER TRADE CLOSED\n\n"
                f"{trade.symbol}\n"
                f"Token\n{trade.token}\n\n"
                f"Entry ${trade.entry_price:.10f}\n"
                f"Exit ${price:.10f}\n\n"
                f"Final Value ${value:.2f}\n"
                f"PnL {pnl:.2f}%\n\n"
                f"Reason: liquidity dropped {LIQUIDITY_DROP_EXIT_PCT:.0f}%"
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

        current_liq = snap["liquidity_usd"]
        current_price = snap["price_usd"]
        exit_floor = pos["entry_liquidity_usd"] * (1 - LIQUIDITY_DROP_EXIT_PCT / 100.0)

        send(
            f"📊 LIVE POSITION UPDATE\n\n"
            f"{pos['symbol']}\n"
            f"Token\n{pos['token']}\n\n"
            f"Current Price ${current_price:.10f}\n"
            f"Entry Liquidity ${pos['entry_liquidity_usd']:,.0f}\n"
            f"Current Liquidity ${current_liq:,.0f}"
        )

        if current_liq <= exit_floor:
            execute_live_sell(pos, current_price, current_liq)
            LIVE_POSITIONS.pop(token, None)
            return

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
# PROCESS NEW PAIR
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

def process_pair(token: str, pair: str):
    with LOCK:
        if pair in ACTIVE or pair in SEEN_PAIRS:
            return
        ACTIVE.add(pair)

    try:
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
                f"Token\n{token}\n\n"
                f"Pair\n{pair}\n\n"
                f"Reason: {security_reason}"
            )
            return

        age = snap["age_seconds"] or 0
        send(
            f"🚀 NEW LAUNCH DETECTED\n\n"
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
            SEEN_PAIRS.add(pair)

        if RUN_PURCHASE == "on":
            if len(LIVE_POSITIONS) >= MAX_OPEN_TRADES:
                send(f"⚠️ LIVE TRADE SKIPPED\nReason: max open trades reached\nOpen Trades {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}")
                return
            pos = execute_live_buy(token, pair, snap["liquidity_usd"])
            if pos:
                LIVE_POSITIONS[token] = pos
                threading.Thread(target=monitor_live_position, args=(token,), daemon=True).start()
        else:
            open_paper_trade(token, pair, snap)

    finally:
        with LOCK:
            ACTIVE.discard(pair)

# -------------------------
# EVENT LISTENER
# -------------------------
def event_listener():
    last_block = w3.eth.block_number
    send("Listening for new V2 pairs...")

    while True:
        try:
            block = w3.eth.block_number
            if block > last_block:
                events = factory.events.PairCreated.get_logs(
                    from_block=last_block + 1,
                    to_block=block
                )

                for e in events:
                    token0 = e["args"]["token0"]
                    token1 = e["args"]["token1"]
                    pair = e["args"]["pair"]

                    token = None
                    if token0.lower() == WETH.lower():
                        token = token1
                    elif token1.lower() == WETH.lower():
                        token = token0

                    if token:
                        process_pair(
                            Web3.to_checksum_address(token),
                            Web3.to_checksum_address(pair)
                        )

                last_block = block

            time.sleep(EVENT_POLL_SECONDS)

        except Exception as e:
            print("event listener error", e)
            time.sleep(3)

# -------------------------
# PORTFOLIO
# -------------------------
def portfolio_loop():
    while True:
        total = ACCOUNT_CASH

        for trade in PAPER_TRADES.values():
            snap = get_pair_snapshot(trade.pair)
            if not snap:
                continue
            total += trade.tokens * snap["price_usd"]

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
            f"Block {w3.eth.block_number}\n"
            f"Mode {'LIVE' if RUN_PURCHASE == 'on' else 'PAPER'}\n"
            f"Paper Trades {len(PAPER_TRADES)}/{MAX_OPEN_TRADES}\n"
            f"Live Positions {len(LIVE_POSITIONS)}/{MAX_OPEN_TRADES}\n"
            f"Seen Pairs {len(SEEN_PAIRS)}"
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
        f"Entry Rules: volume there and sells exist\n"
        f"Security Rules: sell tax <= {MAX_SELL_TAX_PCT:.2f}% | buy tax <= {MAX_BUY_TAX_PCT:.2f}%\n"
        f"Exit Rule: liquidity drop {LIQUIDITY_DROP_EXIT_PCT:.0f}%"
    )

    threading.Thread(target=event_listener, daemon=True).start()
    threading.Thread(target=portfolio_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
