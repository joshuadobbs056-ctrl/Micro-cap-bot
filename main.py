import subprocess
import sys


def ensure_package(package_name: str, import_name: str | None = None) -> None:
    target = import_name or package_name
    try:
        __import__(target)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])


ensure_package("websockets")
ensure_package("requests")
ensure_package("web3")

import asyncio
import json
import os
import time

import requests
import websockets
from web3 import Web3

# ---------------------------
# ENV / CONFIG
# ---------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Ethereum "money signal" scanner (main alpha)
NODE = os.getenv("NODE", "")
PAIR_POLL_SECONDS = float(os.getenv("PAIR_POLL_SECONDS", "2"))
TRACK_WINDOW_SECONDS = int(os.getenv("TRACK_WINDOW_SECONDS", "90"))
ETH_MAX_TRACK_SECONDS = int(os.getenv("ETH_MAX_TRACK_SECONDS", "300"))
MIN_ETH_LIQUIDITY = float(os.getenv("MIN_ETH_LIQUIDITY", "1.5"))
MAX_ETH_LIQUIDITY = float(os.getenv("MAX_ETH_LIQUIDITY", "30"))
MONEY_MIN_BUYS = int(os.getenv("MONEY_MIN_BUYS", "6"))
MONEY_MIN_UNIQUE_BUYERS = int(os.getenv("MONEY_MIN_UNIQUE_BUYERS", "5"))
MONEY_MIN_BUY_ETH = float(os.getenv("MONEY_MIN_BUY_ETH", "0.8"))
MONEY_MIN_BUYER_VELOCITY = float(os.getenv("MONEY_MIN_BUYER_VELOCITY", "0.05"))
MONEY_REQUIRE_HONEYPOT_PASS = os.getenv("MONEY_REQUIRE_HONEYPOT_PASS", "true").lower() == "true"

# Pump.fun secondary signal
PUMP_WS = os.getenv("PUMP_WS", "wss://pumpportal.fun/api/data")
PUMP_MAX_SIGNAL_AGE = int(os.getenv("PUMP_MAX_SIGNAL_AGE", "90"))
PUMP_TRACK_MAX_SECONDS = int(os.getenv("PUMP_TRACK_MAX_SECONDS", "180"))
PUMP_MIN_BUYS = int(os.getenv("PUMP_MIN_BUYS", "18"))
PUMP_MIN_UNIQUE_BUYERS = int(os.getenv("PUMP_MIN_UNIQUE_BUYERS", "14"))
PUMP_MIN_SOL_VOLUME = float(os.getenv("PUMP_MIN_SOL_VOLUME", "8"))
PUMP_MIN_BUYER_VELOCITY = float(os.getenv("PUMP_MIN_BUYER_VELOCITY", "0.16"))
PUMP_MIN_BONDING_SOL = float(os.getenv("PUMP_MIN_BONDING_SOL", "18"))
PUMP_MIN_SCORE = int(os.getenv("PUMP_MIN_SCORE", "70"))

# Static addresses
ETH_FACTORY = Web3.to_checksum_address("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f")
WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2")

# ---------------------------
# WEB3
# ---------------------------
w3 = Web3(Web3.HTTPProvider(NODE)) if NODE else None

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

PAIR_ABI = [
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
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

factory_contract = w3.eth.contract(address=ETH_FACTORY, abi=FACTORY_ABI) if w3 and w3.is_connected() else None

# ---------------------------
# STATE
# ---------------------------
ETH_TRACKED: dict[str, dict] = {}
PUMP_TRACKED: dict[str, dict] = {}
eth_last_block = 0
eth_price_cache = {"value": 3000.0, "ts": 0.0}

# ---------------------------
# HELPERS
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
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
        except Exception as exc:
            print(f"Telegram error: {exc}")
    print(msg)


def send_token_bubble(label: str, value: str) -> None:
    send(f"{label}\n`{value}`")


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def get_first(d: dict, keys: list[str], default=None):
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def short_addr(addr: str) -> str:
    if not addr:
        return "unknown"
    if len(addr) < 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def dextools_link(pair: str) -> str:
    return f"https://www.dextools.io/app/en/ether/pair-explorer/{pair}"


def etherscan_token_link(token: str) -> str:
    return f"https://etherscan.io/token/{token}"


def dexscreener_sol_link(mint: str) -> str:
    return f"https://dexscreener.com/solana/{mint}"


def gmgn_link(mint: str) -> str:
    return f"https://gmgn.ai/sol/token/{mint}"


def now_ts() -> float:
    return time.time()


def get_eth_price_usd() -> float:
    if now_ts() - eth_price_cache["ts"] < 120:
        return eth_price_cache["value"]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "ethereum", "vs_currencies": "usd"},
            timeout=10,
        )
        data = r.json()
        value = safe_float(data.get("ethereum", {}).get("usd"), eth_price_cache["value"])
        if value > 0:
            eth_price_cache["value"] = value
            eth_price_cache["ts"] = now_ts()
    except Exception:
        pass
    return eth_price_cache["value"]


def honeypot_pass(token: str) -> bool:
    url = f"https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses={token}"
    try:
        data = requests.get(url, timeout=10).json()
        result = data.get("result", {}).get(token.lower(), {})
        is_honeypot = result.get("is_honeypot")
        if is_honeypot is None:
            return False
        return is_honeypot == "0"
    except Exception:
        return False


def get_token_name_symbol(token: str) -> tuple[str, str]:
    try:
        contract = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
        name = contract.functions.name().call()
        symbol = contract.functions.symbol().call()
        return str(name), str(symbol)
    except Exception:
        return "Unknown", "?"


def get_pair_liquidity_eth(pair: str) -> float:
    try:
        pair_contract = w3.eth.contract(address=Web3.to_checksum_address(pair), abi=PAIR_ABI)
        reserve0, reserve1, _ = pair_contract.functions.getReserves().call()
        token0 = pair_contract.functions.token0().call()
        token1 = pair_contract.functions.token1().call()
        weth_reserve = reserve0 if token0.lower() == WETH.lower() else reserve1 if token1.lower() == WETH.lower() else 0
        return float(w3.from_wei(weth_reserve, "ether"))
    except Exception:
        return 0.0


def init_eth_token_state(token: str, pair: str, created_block: int) -> dict:
    name, symbol = get_token_name_symbol(token)
    liq_eth = get_pair_liquidity_eth(pair)
    honeypot_ok = honeypot_pass(token) if MONEY_REQUIRE_HONEYPOT_PASS else True
    return {
        "token": token,
        "pair": pair,
        "name": name,
        "symbol": symbol,
        "created_at": now_ts(),
        "created_block": created_block,
        "last_seen_block": created_block,
        "liquidity_eth": liq_eth,
        "honeypot_ok": honeypot_ok,
        "buys": 0,
        "buy_eth": 0.0,
        "buyers": set(),
        "money_sent": False,
        "swap_from_block": created_block,
    }


def init_pump_state(payload: dict) -> dict:
    return {
        "mint": get_first(payload, ["mint", "tokenAddress", "address"]),
        "name": str(get_first(payload, ["name", "tokenName"], "Unknown")),
        "symbol": str(get_first(payload, ["symbol", "ticker", "tokenSymbol"], "?")),
        "created_at": now_ts(),
        "buys": 0,
        "buy_sol": 0.0,
        "buyers": set(),
        "alerted": False,
        "bonding_sol": 0.0,
        "score": 0,
    }

# ---------------------------
# ETH MONEY SIGNAL
# ---------------------------
def maybe_send_money_signal(state: dict) -> None:
    if state["money_sent"]:
        return

    age = now_ts() - state["created_at"]
    if age > TRACK_WINDOW_SECONDS:
        return

    unique_buyers = len(state["buyers"])
    buyer_velocity = unique_buyers / max(age, 1)
    liq_eth = state["liquidity_eth"]

    if MONEY_REQUIRE_HONEYPOT_PASS and not state["honeypot_ok"]:
        return
    if liq_eth < MIN_ETH_LIQUIDITY or liq_eth > MAX_ETH_LIQUIDITY:
        return
    if state["buys"] < MONEY_MIN_BUYS:
        return
    if unique_buyers < MONEY_MIN_UNIQUE_BUYERS:
        return
    if state["buy_eth"] < MONEY_MIN_BUY_ETH:
        return
    if buyer_velocity < MONEY_MIN_BUYER_VELOCITY:
        return

    state["money_sent"] = True
    liq_usd = liq_eth * get_eth_price_usd()
    msg = (
        "💰 *MONEY SIGNAL*\n\n"
        f"*{state['name']} ({state['symbol']})*\n"
        f"Age: {int(age)}s\n"
        f"Liquidity: {liq_eth:.2f} ETH (~${liq_usd:,.0f})\n"
        f"Buys: {state['buys']}\n"
        f"Buy volume: {state['buy_eth']:.3f} ETH\n"
        f"Unique buyers: {unique_buyers}\n"
        f"Buyer velocity: {buyer_velocity:.3f}/s\n"
        f"Honeypot check: {'PASS' if state['honeypot_ok'] else 'FAIL'}\n\n"
        f"DexTools\n{dextools_link(state['pair'])}\n\n"
        f"Etherscan\n{etherscan_token_link(state['token'])}"
    )
    send(msg)
    send_token_bubble("TOKEN", state["token"])
    send_token_bubble("PAIR", state["pair"])


def process_new_eth_pairs(from_block: int, to_block: int) -> None:
    if not factory_contract:
        return

    try:
        events = factory_contract.events.PairCreated.get_logs(from_block=from_block, to_block=to_block)
    except Exception as exc:
        print(f"PairCreated fetch error: {exc}")
        return

    for event in events:
        token0 = event["args"]["token0"]
        token1 = event["args"]["token1"]
        pair = event["args"]["pair"]

        if token0.lower() == WETH.lower() and token1.lower() != WETH.lower():
            token = token1
        elif token1.lower() == WETH.lower() and token0.lower() != WETH.lower():
            token = token0
        else:
            continue

        if token in ETH_TRACKED:
            continue

        state = init_eth_token_state(token, pair, event["blockNumber"])
        ETH_TRACKED[token] = state
        send(
            "🟢 *ETH NEW PAIR WATCH*\n\n"
            f"*{state['name']} ({state['symbol']})*\n"
            f"Liquidity: {state['liquidity_eth']:.2f} ETH\n"
            f"Honeypot check: {'PASS' if state['honeypot_ok'] else 'FAIL'}"
        )
        send_token_bubble("TOKEN", token)
        send_token_bubble("PAIR", pair)


def process_eth_swaps_for_state(state: dict, current_block: int) -> None:
    try:
        pair_contract = w3.eth.contract(address=Web3.to_checksum_address(state["pair"]), abi=PAIR_ABI)
        swap_event = pair_contract.events.Swap
        logs = swap_event.get_logs(from_block=state["swap_from_block"], to_block=current_block)
    except Exception as exc:
        print(f"Swap fetch error for {state['pair']}: {exc}")
        return

    if logs:
        state["swap_from_block"] = current_block + 1
        state["last_seen_block"] = current_block

    try:
        pair_contract = w3.eth.contract(address=Web3.to_checksum_address(state["pair"]), abi=PAIR_ABI)
        token0 = pair_contract.functions.token0().call()
        token1 = pair_contract.functions.token1().call()
    except Exception as exc:
        print(f"Pair token read error for {state['pair']}: {exc}")
        return

    for log in logs:
        args = log["args"]
        amount0_in = safe_float(args["amount0In"])
        amount1_in = safe_float(args["amount1In"])
        amount0_out = safe_float(args["amount0Out"])
        amount1_out = safe_float(args["amount1Out"])
        to_addr = args["to"]

        is_buy = False
        eth_in = 0.0

        if token0.lower() == WETH.lower() and amount0_in > 0 and amount1_out > 0:
            is_buy = True
            eth_in = float(w3.from_wei(int(amount0_in), "ether"))
        elif token1.lower() == WETH.lower() and amount1_in > 0 and amount0_out > 0:
            is_buy = True
            eth_in = float(w3.from_wei(int(amount1_in), "ether"))

        if is_buy:
            state["buys"] += 1
            state["buy_eth"] += eth_in
            if to_addr:
                state["buyers"].add(str(to_addr))

    state["liquidity_eth"] = get_pair_liquidity_eth(state["pair"])
    maybe_send_money_signal(state)


async def eth_scanner_loop() -> None:
    global eth_last_block
    if not w3 or not w3.is_connected() or not factory_contract:
        send("⚠️ ETH scanner disabled (missing or invalid NODE)")
        return

    send("✅ ETH money-signal scanner started")
    eth_last_block = w3.eth.block_number

    while True:
        try:
            current_block = w3.eth.block_number
            if current_block > eth_last_block:
                process_new_eth_pairs(eth_last_block + 1, current_block)
                eth_last_block = current_block

            for token, state in list(ETH_TRACKED.items()):
                age = now_ts() - state["created_at"]
                if age > ETH_MAX_TRACK_SECONDS:
                    ETH_TRACKED.pop(token, None)
                    continue
                process_eth_swaps_for_state(state, current_block)
        except Exception as exc:
            print(f"ETH scanner loop error: {exc}")
        await asyncio.sleep(PAIR_POLL_SECONDS)

# ---------------------------
# PUMP.FUN SECONDARY SIGNAL
# ---------------------------
def pump_score(state: dict) -> tuple[int, dict]:
    age = now_ts() - state["created_at"]
    buyers = len(state["buyers"])
    buyer_velocity = buyers / max(age, 1)
    bonding_sol = state["bonding_sol"]

    if age > PUMP_MAX_SIGNAL_AGE:
        return 0, {
            "age": age,
            "buyers": buyers,
            "buyer_velocity": buyer_velocity,
            "bonding_sol": bonding_sol,
        }

    score = 0
    score += min(35, int(buyer_velocity * 100))
    score += min(25, int(bonding_sol))
    score += min(20, int(state["buy_sol"] * 2))
    score += min(20, buyers)

    return min(score, 100), {
        "age": age,
        "buyers": buyers,
        "buyer_velocity": buyer_velocity,
        "bonding_sol": bonding_sol,
    }


def maybe_send_pump_alert(state: dict) -> None:
    if state["alerted"]:
        return

    score, metrics = pump_score(state)
    age = metrics["age"]
    buyers = metrics["buyers"]
    buyer_velocity = metrics["buyer_velocity"]
    bonding_sol = metrics["bonding_sol"]

    if age > PUMP_MAX_SIGNAL_AGE:
        return
    if state["buys"] < PUMP_MIN_BUYS:
        return
    if buyers < PUMP_MIN_UNIQUE_BUYERS:
        return
    if state["buy_sol"] < PUMP_MIN_SOL_VOLUME:
        return
    if buyer_velocity < PUMP_MIN_BUYER_VELOCITY:
        return
    if bonding_sol < PUMP_MIN_BONDING_SOL:
        return
    if score < PUMP_MIN_SCORE:
        return

    state["alerted"] = True
    state["score"] = score
    msg = (
        "🚨 *PUMPFUN SECONDARY SIGNAL*\n\n"
        f"*{state['name']} ({state['symbol']})*\n"
        f"Score: {score}/100\n"
        f"Age: {int(age)}s\n"
        f"Buys: {state['buys']}\n"
        f"Buy volume: {state['buy_sol']:.2f} SOL\n"
        f"Unique buyers: {buyers}\n"
        f"Buyer velocity: {buyer_velocity:.3f}/s\n"
        f"Bonding curve SOL: {bonding_sol:.2f}\n\n"
        f"DexScreener\n{dexscreener_sol_link(state['mint'])}\n\n"
        f"GMGN\n{gmgn_link(state['mint'])}"
    )
    send(msg)
    send_token_bubble("MINT", state["mint"])


def handle_pump_new_token(payload: dict) -> str | None:
    mint = get_first(payload, ["mint", "tokenAddress", "address"])
    if not mint or mint in PUMP_TRACKED:
        return None
    PUMP_TRACKED[mint] = init_pump_state(payload)
    return mint


def handle_pump_trade(payload: dict) -> None:
    mint = get_first(payload, ["mint", "tokenAddress", "address"])
    if not mint or mint not in PUMP_TRACKED:
        return

    side = str(get_first(payload, ["txType", "type", "side", "action"], "")).lower()
    if "buy" not in side:
        return

    state = PUMP_TRACKED[mint]
    state["buys"] += 1
    state["buy_sol"] += safe_float(get_first(payload, ["solAmount", "amountSol", "volumeSol"], 0.0))
    wallet = str(get_first(payload, ["traderPublicKey", "wallet", "maker", "user"], ""))
    if wallet:
        state["buyers"].add(wallet)

    bonding_candidate = max(
        safe_float(get_first(payload, ["vSolInBondingCurve"], 0.0)),
        safe_float(get_first(payload, ["bondingCurveSol"], 0.0)),
        safe_float(get_first(payload, ["virtualSolReserves"], 0.0)),
    )
    if bonding_candidate > state["bonding_sol"]:
        state["bonding_sol"] = bonding_candidate

    maybe_send_pump_alert(state)


async def pump_scanner_loop() -> None:
    backoff = 2
    send("✅ Pump.fun secondary scanner started")
    while True:
        try:
            async with websockets.connect(PUMP_WS, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                backoff = 2

                while True:
                    raw = await ws.recv()
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        continue

                    new_mint = None
                    if get_first(payload, ["mint", "tokenAddress", "address"]) and any(
                        k in payload for k in ["name", "symbol", "ticker", "tokenName"]
                    ):
                        new_mint = handle_pump_new_token(payload)

                    if new_mint:
                        try:
                            await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [new_mint]}))
                        except Exception as exc:
                            print(f"Pump trade subscribe error: {exc}")

                    if any(k in payload for k in ["txType", "type", "side", "action"]):
                        handle_pump_trade(payload)

                    cutoff = now_ts() - PUMP_TRACK_MAX_SECONDS
                    for mint, state in list(PUMP_TRACKED.items()):
                        if state["created_at"] < cutoff:
                            PUMP_TRACKED.pop(mint, None)
        except Exception as exc:
            print(f"Pump websocket error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

# ---------------------------
# MAIN
# ---------------------------
async def main() -> None:
    send("🚀 Dual scanner started: money signal + pumpfun secondary")
    tasks = [pump_scanner_loop()]
    if NODE:
        tasks.append(eth_scanner_loop())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
