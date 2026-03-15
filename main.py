import subprocess
import sys


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

import os
import time
import threading
from collections import defaultdict, deque


# -------------------------
# ENV CONFIG
# -------------------------
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
RUN_PURCHASE = os.getenv("RUN_PURCHASE", "off").lower()

PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD", "50"))

# On-chain V2 ETH liquidity filters
MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY", "0"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY", "999999"))

TRACK_SECONDS = int(os.getenv("TRACK_SECONDS", "30"))
PAIR_POLL_SECONDS = float(os.getenv("PAIR_POLL_SECONDS", "1.5"))
BLOCK_POLL_SECONDS = float(os.getenv("BLOCK_POLL_SECONDS", "2"))
DEXSCREENER_POLL_SECONDS = float(os.getenv("DEXSCREENER_POLL_SECONDS", "20"))

MONEY_MIN_BUYS = int(os.getenv("MONEY_MIN_BUYS", "1"))
MONEY_MIN_UNIQUE_BUYERS = int(os.getenv("MONEY_MIN_UNIQUE_BUYERS", "1"))
MONEY_MIN_BUY_ETH = float(os.getenv("MONEY_MIN_BUY_ETH", "0.03"))
MONEY_MIN_BUYER_VELOCITY = float(os.getenv("MONEY_MIN_BUYER_VELOCITY", "0.2"))
MAX_TOP_BUYER_SHARE = float(os.getenv("MAX_TOP_BUYER_SHARE", "0.95"))

# paper trade settings
PAPER_TRADE_HOLD_SECONDS = int(os.getenv("PAPER_TRADE_HOLD_SECONDS", "180"))
PAPER_MIN_PROFIT_PCT = float(os.getenv("PAPER_MIN_PROFIT_PCT", "15"))
PAPER_STOP_LOSS_PCT = float(os.getenv("PAPER_STOP_LOSS_PCT", "-15"))

# momentum
ENABLE_MOMENTUM_SPIKE = os.getenv("ENABLE_MOMENTUM_SPIKE", "true").lower() == "true"
MOMENTUM_LOOKBACK_SECONDS = int(os.getenv("MOMENTUM_LOOKBACK_SECONDS", "20"))
MOMENTUM_SPIKE_MULTIPLIER = float(os.getenv("MOMENTUM_SPIKE_MULTIPLIER", "3.0"))

# DexScreener
USE_DEXSCREENER = os.getenv("USE_DEXSCREENER", "true").lower() == "true"
DEXSCREENER_TIMEOUT = int(os.getenv("DEXSCREENER_TIMEOUT", "10"))
DEXS_MIN_LIQ_USD = float(os.getenv("DEXS_MIN_LIQ_USD", "3000"))
DEXS_MIN_BUYS_5M = int(os.getenv("DEXS_MIN_BUYS_5M", "1"))
DEXS_REQUIRE_PAIR_FOUND = os.getenv("DEXS_REQUIRE_PAIR_FOUND", "false").lower() == "true"
DEXS_MAX_AGE_MINUTES = int(os.getenv("DEXS_MAX_AGE_MINUTES", "240"))
DEXS_SEARCH_LIMIT = int(os.getenv("DEXS_SEARCH_LIMIT", "8"))

# proof-of-life
SEND_STARTUP_HEARTBEAT = os.getenv("SEND_STARTUP_HEARTBEAT", "true").lower() == "true"
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "300"))
ALERT_NEW_PAIRS = os.getenv("ALERT_NEW_PAIRS", "true").lower() == "true"
ALERT_TRACKING_START = os.getenv("ALERT_TRACKING_START", "true").lower() == "true"
ALERT_REJECTIONS = os.getenv("ALERT_REJECTIONS", "true").lower() == "true"

# debug
DEBUG_DEX = os.getenv("DEBUG_DEX", "true").lower() == "true"
DEBUG_EVENTS = os.getenv("DEBUG_EVENTS", "true").lower() == "true"
DEBUG_DISCOVERY_ALERTS = os.getenv("DEBUG_DISCOVERY_ALERTS", "true").lower() == "true"

# discovery sources
ENABLE_UNISWAP_V2 = os.getenv("ENABLE_UNISWAP_V2", "true").lower() == "true"
ENABLE_UNISWAP_V3 = os.getenv("ENABLE_UNISWAP_V3", "true").lower() == "true"
ENABLE_DEXSCREENER_DISCOVERY = os.getenv("ENABLE_DEXSCREENER_DISCOVERY", "true").lower() == "true"


# -------------------------
# WEB3
# -------------------------
if not NODE:
    raise RuntimeError("NODE required")

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
# CONSTANTS
# -------------------------
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")
USDC = Web3.to_checksum_address("0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
USDT = Web3.to_checksum_address("0xdAC17F958D2ee523a2206206994597C13D831ec7")

UNISWAP_V2_FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
UNISWAP_V3_FACTORY = Web3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")


# -------------------------
# TELEGRAM
# -------------------------
def send(msg: str):
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id": CHAT_ID,
                    "text": msg,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
        except Exception as e:
            print("Telegram error:", e)

    print(msg)


# -------------------------
# HELPERS
# -------------------------
def now_ts() -> float:
    return time.time()


def format_bool(v: bool) -> str:
    return "ON" if v else "OFF"


def purchases_enabled() -> bool:
    return RUN_PURCHASE == "on" and ACCOUNT is not None


def dextools_link(pair: str) -> str:
    return f"https://www.dextools.io/app/en/ether/pair-explorer/{pair}"


def dexscreener_link(pair: str) -> str:
    return f"https://dexscreener.com/ethereum/{pair}"


def reject(symbol: str, reason: str):
    msg = f"⛔ REJECTED {symbol}: {reason}"
    if ALERT_REJECTIONS:
        send(msg)
    else:
        print(msg)


def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def parse_created_at_minutes(created_ms) -> float:
    try:
        if created_ms is None:
            return 999999.0
        return (now_ts() * 1000.0 - float(created_ms)) / 60000.0
    except Exception:
        return 999999.0


# -------------------------
# PAPER TRADE
# -------------------------
class PaperTrade:
    def __init__(self, token: str, name: str, symbol: str, entry_eth: float, pair: str):
        self.token = token
        self.name = name
        self.symbol = symbol
        self.entry = entry_eth
        self.pair = pair
        self.open = now_ts()


PAPER_TRADES = {}
PAPER_LOCK = threading.Lock()


def open_paper_trade(token: str, name: str, symbol: str, entry_eth: float, pair: str):
    with PAPER_LOCK:
        if token in PAPER_TRADES:
            return
        PAPER_TRADES[token] = PaperTrade(token, name, symbol, entry_eth, pair)

    send(
        f"""🧪 PAPER TRADE OPENED

{name} ({symbol})

Entry: {entry_eth:.4f} ETH
Token
{token}

Pair
{pair}"""
    )

    threading.Thread(target=monitor_paper_trade, args=(token,), daemon=True).start()


def close_paper_trade(token: str, exit_eth: float, reason: str):
    with PAPER_LOCK:
        trade = PAPER_TRADES.get(token)
        if not trade:
            return

        pnl = ((exit_eth / trade.entry) - 1.0) * 100 if trade.entry > 0 else 0.0

        send(
            f"""🧪 PAPER TRADE CLOSED

{trade.name} ({trade.symbol})

Reason: {reason}
Entry: {trade.entry:.4f}
Exit: {exit_eth:.4f}

PnL: {pnl:.2f}%"""
        )

        del PAPER_TRADES[token]


def monitor_paper_trade(token: str):
    time.sleep(PAPER_TRADE_HOLD_SECONDS)

    with PAPER_LOCK:
        trade = PAPER_TRADES.get(token)
        if not trade:
            return

    current_liq = get_liquidity(trade.pair)
    if current_liq <= 0:
        ds = fetch_dexscreener_by_pair(trade.pair)
        liq_usd = safe_float((ds or {}).get("liquidity_usd"), 0.0)

        if liq_usd <= 0:
            exit_eth = trade.entry * 0.50
            reason = "liquidity vanished"
        else:
            ratio = max(0.25, min(liq_usd / max(DEXS_MIN_LIQ_USD, 1.0), 3.0))
            exit_eth = trade.entry * ratio
            reason = "paper timed exit (dex liquidity)"
    else:
        entry_liq = max(trade.entry, 0.0001)
        ratio = current_liq / entry_liq
        ratio = max(0.25, min(ratio, 3.0))
        exit_eth = trade.entry * ratio

        pnl_pct = ((exit_eth / trade.entry) - 1.0) * 100.0
        if pnl_pct >= PAPER_MIN_PROFIT_PCT:
            reason = "paper take profit window"
        elif pnl_pct <= PAPER_STOP_LOSS_PCT:
            reason = "paper stop loss window"
        else:
            reason = "paper timed exit"

    close_paper_trade(token, exit_eth, reason)


# -------------------------
# SMART WALLET MEMORY
# -------------------------
SMART_WALLETS = set()


def remember_wallet(wallet: str, profit_pct: float):
    if wallet and profit_pct > 200:
        SMART_WALLETS.add(wallet.lower())


def wallet_is_smart(wallet: str) -> bool:
    return bool(wallet) and wallet.lower() in SMART_WALLETS


# -------------------------
# DEXSCREENER
# -------------------------
def fetch_dexscreener_by_pair(pair: str) -> dict:
    if not USE_DEXSCREENER:
        return {}

    url = f"https://api.dexscreener.com/latest/dex/pairs/ethereum/{pair}"
    try:
        if DEBUG_DEX:
            print(f"DEX PAIR LOOKUP: {url}")
        r = requests.get(url, timeout=DEXSCREENER_TIMEOUT)
        data = r.json()
        pairs = data.get("pairs") or []
        if not pairs:
            if DEBUG_DEX:
                print(f"DEX PAIR LOOKUP EMPTY for {pair}")
            return {}
        p = pairs[0]
        return {
            "pair_address": p.get("pairAddress", ""),
            "base_address": ((p.get("baseToken") or {}).get("address") or ""),
            "base_symbol": ((p.get("baseToken") or {}).get("symbol") or ""),
            "quote_symbol": ((p.get("quoteToken") or {}).get("symbol") or ""),
            "price_usd": p.get("priceUsd"),
            "fdv": p.get("fdv"),
            "liquidity_usd": ((p.get("liquidity") or {}).get("usd")),
            "txns_5m_buys": (((p.get("txns") or {}).get("m5") or {}).get("buys")),
            "txns_5m_sells": (((p.get("txns") or {}).get("m5") or {}).get("sells")),
            "volume_5m": ((p.get("volume") or {}).get("m5")),
            "price_change_5m": ((p.get("priceChange") or {}).get("m5")),
            "url": p.get("url", ""),
            "created_at_ms": p.get("pairCreatedAt"),
            "dex_id": p.get("dexId", ""),
        }
    except Exception as e:
        print("dexscreener pair lookup error:", e)
        return {}


def fetch_dexscreener_candidates() -> list:
    if not ENABLE_DEXSCREENER_DISCOVERY:
        return []

    # wider set of searches than just "ethereum"
    queries = [
        "WETH",
        "ETH",
        "USDC",
        "USDT",
        "new",
        "uniswap",
    ]
    results = []

    for q in queries:
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={q}"
            if DEBUG_DEX:
                print(f"Calling DexScreener search API: {url}")
            r = requests.get(url, timeout=DEXSCREENER_TIMEOUT)
            data = r.json()

            if DEBUG_DEX:
                print(f"DexScreener raw keys for query {q}: {list(data.keys())}")
                print(f"DexScreener raw pair count for query {q}: {len(data.get('pairs') or [])}")

            pairs = data.get("pairs") or []

            for p in pairs[:DEXS_SEARCH_LIMIT]:
                chain_id = str(p.get("chainId", "")).lower()
                if chain_id != "ethereum":
                    continue

                pair_addr = p.get("pairAddress", "")
                base_addr = ((p.get("baseToken") or {}).get("address") or "")
                if not pair_addr or not base_addr:
                    continue

                created_mins = parse_created_at_minutes(p.get("pairCreatedAt"))
                if created_mins > DEXS_MAX_AGE_MINUTES:
                    continue

                results.append(
                    {
                        "pair_address": pair_addr,
                        "base_address": base_addr,
                        "base_symbol": ((p.get("baseToken") or {}).get("symbol") or ""),
                        "quote_symbol": ((p.get("quoteToken") or {}).get("symbol") or ""),
                        "price_usd": p.get("priceUsd"),
                        "fdv": p.get("fdv"),
                        "liquidity_usd": ((p.get("liquidity") or {}).get("usd")),
                        "txns_5m_buys": (((p.get("txns") or {}).get("m5") or {}).get("buys")),
                        "txns_5m_sells": (((p.get("txns") or {}).get("m5") or {}).get("sells")),
                        "volume_5m": ((p.get("volume") or {}).get("m5")),
                        "price_change_5m": ((p.get("priceChange") or {}).get("m5")),
                        "url": p.get("url", ""),
                        "created_at_ms": p.get("pairCreatedAt"),
                        "dex_id": p.get("dexId", ""),
                    }
                )
        except Exception as e:
            print("dexscreener discovery error:", e)

    dedup = {}
    for item in results:
        dedup[item["pair_address"].lower()] = item

    final_results = list(dedup.values())
    if DEBUG_DEX:
        print(f"DEX DISCOVERY FINAL COUNT: {len(final_results)}")
    return final_results


def dexscreener_passes(ds: dict) -> bool:
    if not USE_DEXSCREENER:
        return True

    if not ds:
        return not DEXS_REQUIRE_PAIR_FOUND

    liq_usd = float(ds.get("liquidity_usd") or 0.0)
    buys_5m = int(ds.get("txns_5m_buys") or 0)

    if liq_usd < DEXS_MIN_LIQ_USD:
        return False

    if buys_5m < DEXS_MIN_BUYS_5M:
        return False

    return True


# -------------------------
# ABIs
# -------------------------
UNISWAP_V2_FACTORY_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": False, "name": "pair", "type": "address"},
        ],
        "name": "PairCreated",
        "type": "event",
    }
]

UNISWAP_V3_FACTORY_ABI = [
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

PAIR_ABI = [
    {
        "name": "getReserves",
        "outputs": [
            {"type": "uint112"},
            {"type": "uint112"},
            {"type": "uint32"},
        ],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "token0",
        "outputs": [{"type": "address"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "token1",
        "outputs": [{"type": "address"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
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
]

v2_factory = w3.eth.contract(address=UNISWAP_V2_FACTORY, abi=UNISWAP_V2_FACTORY_ABI)
v3_factory = w3.eth.contract(address=UNISWAP_V3_FACTORY, abi=UNISWAP_V3_FACTORY_ABI)


# -------------------------
# HELPERS
# -------------------------
def get_pair(pair: str):
    return w3.eth.contract(address=pair, abi=PAIR_ABI)


def get_liquidity(pair: str) -> float:
    try:
        c = get_pair(pair)
        r = c.functions.getReserves().call()
        t0 = c.functions.token0().call()
        t1 = c.functions.token1().call()

        if t0.lower() == WETH.lower():
            return float(w3.from_wei(r[0], "ether"))

        if t1.lower() == WETH.lower():
            return float(w3.from_wei(r[1], "ether"))

    except Exception:
        pass

    return 0.0


def token_info(token: str):
    try:
        c = w3.eth.contract(address=token, abi=ERC20_ABI)
        return c.functions.name().call(), c.functions.symbol().call()
    except Exception:
        return "Unknown", "UNK"


def parse_swap_direction(args: dict, token0: str, token1: str):
    amount0_in = int(args.get("amount0In", 0))
    amount1_in = int(args.get("amount1In", 0))
    amount0_out = int(args.get("amount0Out", 0))
    amount1_out = int(args.get("amount1Out", 0))

    if token0.lower() == WETH.lower():
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount0_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, str(buyer) if buyer else ""
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount0_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, str(seller) if seller else ""

    if token1.lower() == WETH.lower():
        if amount1_in > 0 and amount0_out > 0:
            eth_amount = float(w3.from_wei(amount1_in, "ether"))
            buyer = args.get("to")
            return "buy", eth_amount, str(buyer) if buyer else ""
        if amount0_in > 0 and amount1_out > 0:
            eth_amount = float(w3.from_wei(amount1_out, "ether"))
            seller = args.get("sender")
            return "sell", eth_amount, str(seller) if seller else ""

    return None, 0.0, ""


# -------------------------
# TRACK TOKEN
# -------------------------
ACTIVE_PAIRS = set()
ACTIVE_PAIRS_LOCK = threading.Lock()
SEEN_DISCOVERY = set()
SEEN_DISCOVERY_LOCK = threading.Lock()


def mark_seen(key: str) -> bool:
    with SEEN_DISCOVERY_LOCK:
        if key in SEEN_DISCOVERY:
            return False
        SEEN_DISCOVERY.add(key)
        return True


def process_pair(token: str, pair: str, source: str = "unknown", ds_hint: dict = None):
    pair = Web3.to_checksum_address(pair)
    token = Web3.to_checksum_address(token)

    with ACTIVE_PAIRS_LOCK:
        if pair in ACTIVE_PAIRS:
            return
        ACTIVE_PAIRS.add(pair)

    def worker():
        try:
            ds = ds_hint or fetch_dexscreener_by_pair(pair)
            liquidity = get_liquidity(pair)
            liq_usd = safe_float((ds or {}).get("liquidity_usd"), 0.0)

            if ALERT_TRACKING_START:
                send(
                    f"""🔎 TRACKING STARTED

Source: {source}

Pair
{pair}

Token
{token}

Liquidity ETH: {liquidity:.4f}
Liquidity USD: {liq_usd:,.0f}"""
                )
            else:
                print(f"Tracking pair {pair} | liquidity_eth {liquidity:.4f} | liquidity_usd {liq_usd:.0f} | source {source}")

            has_valid_eth_liquidity = liquidity > 0 and MIN_ETH_LIQUIDITY <= liquidity <= MAX_ETH_LIQUIDITY
            has_valid_usd_liquidity = liq_usd >= DEXS_MIN_LIQ_USD

            if not has_valid_eth_liquidity and not has_valid_usd_liquidity:
                reject("UNKNOWN", f"liquidity eth {liquidity:.4f} and liquidity usd {liq_usd:.0f} failed")
                return

            name, symbol = token_info(token)
            pair_contract = get_pair(pair)
            token0 = pair_contract.functions.token0().call()
            token1 = pair_contract.functions.token1().call()

            buyers = set()
            smart_buyers = set()
            buyer_counts = defaultdict(int)

            buy_eth = 0.0
            sell_eth = 0.0
            buy_count = 0
            sell_count = 0

            recent_unique_buyer_times = deque()
            start = now_ts()
            start_block = w3.eth.block_number

            while now_ts() - start < TRACK_SECONDS:
                try:
                    current_block = w3.eth.block_number

                    if current_block >= start_block:
                        events = pair_contract.events.Swap.get_logs(
                            from_block=start_block,
                            to_block=current_block,
                        )
                        start_block = current_block + 1

                        for ev in events:
                            side, eth_amount, wallet = parse_swap_direction(
                                ev["args"], token0, token1
                            )

                            if side == "buy":
                                buy_count += 1
                                buy_eth += eth_amount
                                if wallet:
                                    wallet = wallet.lower()
                                    is_new = wallet not in buyers
                                    buyers.add(wallet)
                                    buyer_counts[wallet] += 1

                                    if is_new:
                                        recent_unique_buyer_times.append(now_ts())

                                    if wallet_is_smart(wallet):
                                        smart_buyers.add(wallet)

                            elif side == "sell":
                                sell_count += 1
                                sell_eth += eth_amount

                        cutoff = now_ts() - MOMENTUM_LOOKBACK_SECONDS
                        while recent_unique_buyer_times and recent_unique_buyer_times[0] < cutoff:
                            recent_unique_buyer_times.popleft()

                except Exception as e:
                    print(f"swap tracking error for {pair}: {e}")

                time.sleep(PAIR_POLL_SECONDS)

            unique = len(buyers)
            velocity = unique / max(TRACK_SECONDS / 60.0, 0.01)

            spike_velocity = len(recent_unique_buyer_times) / max(MOMENTUM_LOOKBACK_SECONDS / 60.0, 0.01)
            momentum_spike = ENABLE_MOMENTUM_SPIKE and spike_velocity >= max(
                MONEY_MIN_BUYER_VELOCITY * MOMENTUM_SPIKE_MULTIPLIER,
                3.0
            )

            top_buyer_share = 0.0
            if buy_count > 0 and buyer_counts:
                top_buyer_share = max(buyer_counts.values()) / buy_count

            if buy_count < MONEY_MIN_BUYS:
                reject(symbol, f"buy_count {buy_count} < {MONEY_MIN_BUYS}")
                return

            if unique < MONEY_MIN_UNIQUE_BUYERS:
                reject(symbol, f"unique buyers {unique} < {MONEY_MIN_UNIQUE_BUYERS}")
                return

            if buy_eth < MONEY_MIN_BUY_ETH:
                ds_buys_5m = int((ds or {}).get("txns_5m_buys") or 0)
                if ds_buys_5m < max(1, DEXS_MIN_BUYS_5M):
                    reject(symbol, f"buy_eth {buy_eth:.4f} < {MONEY_MIN_BUY_ETH}")
                    return

            if velocity < MONEY_MIN_BUYER_VELOCITY and not momentum_spike:
                reject(symbol, f"velocity {velocity:.2f} < {MONEY_MIN_BUYER_VELOCITY}")
                return

            if top_buyer_share > MAX_TOP_BUYER_SHARE and buy_count > 1:
                reject(symbol, f"top buyer share {top_buyer_share:.2%} > {MAX_TOP_BUYER_SHARE:.2%}")
                return

            if not dexscreener_passes(ds):
                reject(symbol, "failed DexScreener confirmation")
                return

            smart_detected = len(smart_buyers) > 0
            mode = "🧪 WOULD BUY" if RUN_PURCHASE != "on" else "🟢 BUY"

            ds_text = ""
            if ds:
                ds_text = f"""

DexScreener
Liquidity USD: {float(ds.get('liquidity_usd') or 0):,.0f}
Buys 5m: {int(ds.get('txns_5m_buys') or 0)}
Sells 5m: {int(ds.get('txns_5m_sells') or 0)}
Volume 5m: {float(ds.get('volume_5m') or 0):,.0f}
Price Change 5m: {float(ds.get('price_change_5m') or 0):.2f}%
FDV: {float(ds.get('fdv') or 0):,.0f}
Dex ID: {ds.get('dex_id') or ''}
"""

            send(
                f"""{mode}

{name} ({symbol})

Source: {source}
Liquidity ETH: {liquidity:.2f}
Liquidity USD: {liq_usd:,.0f}
Buys: {buy_count}
Sells: {sell_count}
Buy ETH: {buy_eth:.3f}
Sell ETH: {sell_eth:.3f}
Unique buyers: {unique}
Velocity: {velocity:.2f}/min
Spike velocity: {spike_velocity:.2f}/min
Momentum spike: {format_bool(momentum_spike)}
Top buyer share: {top_buyer_share:.0%}
Smart wallets: {"YES" if smart_detected else "NO"}{ds_text}

Token
{token}

Pair
{pair}

DexTools
{dextools_link(pair)}

DexScreener
{dexscreener_link(pair)}
"""
            )

            if RUN_PURCHASE != "on":
                rough_entry = max(min(buy_eth, 2.0), 0.05)
                open_paper_trade(token, name, symbol, rough_entry, pair)

        finally:
            with ACTIVE_PAIRS_LOCK:
                ACTIVE_PAIRS.discard(pair)

    threading.Thread(target=worker, daemon=True).start()


# -------------------------
# DISCOVERY HANDLERS
# -------------------------
def handle_v2_event(e):
    try:
        t0 = e["args"]["token0"]
        t1 = e["args"]["token1"]
        pair = e["args"]["pair"]

        token = None

        if str(t0).lower() == WETH.lower():
            token = t1
        elif str(t1).lower() == WETH.lower():
            token = t0

        if token:
            token = Web3.to_checksum_address(token)
            pair = Web3.to_checksum_address(pair)

            if not mark_seen(f"pair:{pair.lower()}"):
                return

            if ALERT_NEW_PAIRS:
                send(
                    f"""🆕 NEW V2 PAIR DETECTED

Token
{token}

Pair
{pair}"""
                )

            process_pair(token, pair, source="uniswap_v2")
    except Exception as ex:
        send(f"handle_v2_event error: {ex}")
        print("handle_v2_event error:", ex)


def handle_v3_event(e):
    try:
        t0 = e["args"]["token0"]
        t1 = e["args"]["token1"]
        pool = e["args"]["pool"]
        fee = e["args"]["fee"]

        token = None

        if str(t0).lower() == WETH.lower():
            token = t1
        elif str(t1).lower() == WETH.lower():
            token = t0
        else:
            return

        token = Web3.to_checksum_address(token)
        pool = Web3.to_checksum_address(pool)

        if not mark_seen(f"pair:{pool.lower()}"):
            return

        if ALERT_NEW_PAIRS:
            send(
                f"""🆕 NEW V3 POOL DETECTED

Fee: {fee}

Token
{token}

Pool
{pool}"""
            )

        process_pair(token, pool, source="uniswap_v3")
    except Exception as ex:
        send(f"handle_v3_event error: {ex}")
        print("handle_v3_event error:", ex)


def dexscreener_discovery_loop():
    first_tick = True

    while True:
        try:
            if DEBUG_DEX:
                print("DEX LOOP TICK")
            if DEBUG_DISCOVERY_ALERTS and first_tick:
                send("🛰 DexScreener discovery loop entered")
                first_tick = False

            candidates = fetch_dexscreener_candidates()

            if DEBUG_DEX:
                print(f"DEX CANDIDATES FOUND: {len(candidates)}")
            if DEBUG_DISCOVERY_ALERTS:
                send(f"🛰 Dex candidates found: {len(candidates)}")

            for c in candidates:
                pair = c["pair_address"]
                token = c["base_address"]

                if not pair or not token:
                    continue

                quote_symbol = (c.get("quote_symbol") or "").upper()
                if quote_symbol not in {"WETH", "ETH", "USDC", "USDT"}:
                    continue

                if not mark_seen(f"pair:{pair.lower()}"):
                    continue

                if ALERT_NEW_PAIRS:
                    send(
                        f"""🆕 DEXSCREENER CANDIDATE DETECTED

Token
{token}

Pair
{pair}

Created minutes ago: {parse_created_at_minutes(c.get('created_at_ms')):.1f}
Liquidity USD: {safe_float(c.get('liquidity_usd')):,.0f}
Buys 5m: {int(c.get('txns_5m_buys') or 0)}"""
                    )

                try:
                    process_pair(
                        Web3.to_checksum_address(token),
                        Web3.to_checksum_address(pair),
                        source="dexscreener",
                        ds_hint=c,
                    )
                except Exception as e:
                    print("dexscreener candidate process error:", e)

        except Exception as e:
            print("dexscreener discovery loop error", e)
            if DEBUG_DISCOVERY_ALERTS:
                send(f"❌ Dex discovery loop error: {e}")

        time.sleep(DEXSCREENER_POLL_SECONDS)


# -------------------------
# HEARTBEAT
# -------------------------
def heartbeat_loop():
    while True:
        try:
            time.sleep(HEARTBEAT_SECONDS)
            send(
                f"""💓 SCANNER HEARTBEAT

Connected: YES
Current block: {w3.eth.block_number}
Mode: {"LIVE" if purchases_enabled() else "PAPER"}
Active pair trackers: {len(ACTIVE_PAIRS)}
Open paper trades: {len(PAPER_TRADES)}
Seen discoveries: {len(SEEN_DISCOVERY)}
V2: {format_bool(ENABLE_UNISWAP_V2)}
V3: {format_bool(ENABLE_UNISWAP_V3)}
DexScreener discovery: {format_bool(ENABLE_DEXSCREENER_DISCOVERY)}
"""
            )
        except Exception as e:
            print("heartbeat error", e)


# -------------------------
# MAIN LOOP
# -------------------------
def main():
    send(
        f"""ETH Scanner Started

Purchase Mode: {"LIVE" if RUN_PURCHASE == "on" else "PAPER"}
Purchase USD: {PURCHASE_AMOUNT_USD}

Filters
MIN_ETH_LIQUIDITY={MIN_ETH_LIQUIDITY}
MAX_ETH_LIQUIDITY={MAX_ETH_LIQUIDITY}
MONEY_MIN_BUYS={MONEY_MIN_BUYS}
MONEY_MIN_UNIQUE_BUYERS={MONEY_MIN_UNIQUE_BUYERS}
MONEY_MIN_BUY_ETH={MONEY_MIN_BUY_ETH}
MONEY_MIN_BUYER_VELOCITY={MONEY_MIN_BUYER_VELOCITY}
MAX_TOP_BUYER_SHARE={MAX_TOP_BUYER_SHARE}
TRACK_SECONDS={TRACK_SECONDS}

Discovery Sources
ENABLE_UNISWAP_V2={format_bool(ENABLE_UNISWAP_V2)}
ENABLE_UNISWAP_V3={format_bool(ENABLE_UNISWAP_V3)}
ENABLE_DEXSCREENER_DISCOVERY={format_bool(ENABLE_DEXSCREENER_DISCOVERY)}
DEXSCREENER_POLL_SECONDS={DEXSCREENER_POLL_SECONDS}
DEXS_MAX_AGE_MINUTES={DEXS_MAX_AGE_MINUTES}

DexScreener Filters
USE_DEXSCREENER={format_bool(USE_DEXSCREENER)}
DEXS_MIN_LIQ_USD={DEXS_MIN_LIQ_USD}
DEXS_MIN_BUYS_5M={DEXS_MIN_BUYS_5M}

Debug
DEBUG_DEX={format_bool(DEBUG_DEX)}
DEBUG_EVENTS={format_bool(DEBUG_EVENTS)}
DEBUG_DISCOVERY_ALERTS={format_bool(DEBUG_DISCOVERY_ALERTS)}

Proof Of Life
ALERT_NEW_PAIRS={format_bool(ALERT_NEW_PAIRS)}
ALERT_TRACKING_START={format_bool(ALERT_TRACKING_START)}
ALERT_REJECTIONS={format_bool(ALERT_REJECTIONS)}
SEND_STARTUP_HEARTBEAT={format_bool(SEND_STARTUP_HEARTBEAT)}
HEARTBEAT_SECONDS={HEARTBEAT_SECONDS}
"""
    )

    send("✅ Main started")

    if SEND_STARTUP_HEARTBEAT:
        threading.Thread(target=heartbeat_loop, daemon=True).start()

    if ENABLE_DEXSCREENER_DISCOVERY:
        threading.Thread(target=dexscreener_discovery_loop, daemon=True).start()
        send("✅ Dex thread started")

    last_v2_block = w3.eth.block_number
    last_v3_block = last_v2_block

    while True:
        try:
            block = w3.eth.block_number

            if ENABLE_UNISWAP_V2 and block > last_v2_block:
                events = v2_factory.events.PairCreated.get_logs(
                    from_block=last_v2_block + 1,
                    to_block=block,
                )

                if DEBUG_EVENTS:
                    print(f"V2 event count: {len(events)}")
                if events:
                    send(f"📡 Found {len(events)} new V2 pair(s) between blocks {last_v2_block + 1} and {block}")

                for e in events:
                    handle_v2_event(e)

                last_v2_block = block

            if ENABLE_UNISWAP_V3 and block > last_v3_block:
                events = v3_factory.events.PoolCreated.get_logs(
                    from_block=last_v3_block + 1,
                    to_block=block,
                )

                if DEBUG_EVENTS:
                    print(f"V3 event count: {len(events)}")
                if events:
                    send(f"📡 Found {len(events)} new V3 pool(s) between blocks {last_v3_block + 1} and {block}")

                for e in events:
                    handle_v3_event(e)

                last_v3_block = block

            time.sleep(BLOCK_POLL_SECONDS)

        except Exception as e:
            print("loop error", e)
            time.sleep(5)


# -------------------------
# START
# -------------------------
if __name__ == "__main__":
    main()
