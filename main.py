import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict, deque


def ensure_package(package_name: str, import_name: str | None = None) -> None:
    """Install a package at runtime if it is missing."""
    target = import_name or package_name
    try:
        __import__(target)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])


ensure_package("websockets")
ensure_package("requests")

import requests
import websockets

# ---------------------------
# CONFIG
# ---------------------------
PUMPPORTAL_WS = os.getenv("PUMPPORTAL_WS", "wss://pumpportal.fun/api/data")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Detection / alert windows
SCOUT_MIN_AGE_SECONDS = int(os.getenv("SCOUT_MIN_AGE_SECONDS", "0"))
HOT_WINDOW_SECONDS = int(os.getenv("HOT_WINDOW_SECONDS", "75"))
TRACK_MAX_SECONDS = int(os.getenv("TRACK_MAX_SECONDS", "180"))

# Momentum thresholds
MIN_TOTAL_BUYS = int(os.getenv("MIN_TOTAL_BUYS", "8"))
MIN_UNIQUE_BUYERS = int(os.getenv("MIN_UNIQUE_BUYERS", "6"))
MIN_BUY_SOL = float(os.getenv("MIN_BUY_SOL", "3"))
MIN_BUY_SELL_RATIO = float(os.getenv("MIN_BUY_SELL_RATIO", "2.0"))
MIN_MARKET_CAP_USD = float(os.getenv("MIN_MARKET_CAP_USD", "15000"))
MAX_MARKET_CAP_USD = float(os.getenv("MAX_MARKET_CAP_USD", "350000"))

# Sellability filter
REQUIRE_ONE_SUCCESSFUL_SELL = os.getenv("REQUIRE_ONE_SUCCESSFUL_SELL", "true").lower() == "true"

# Noise control
MIN_NAME_LENGTH = int(os.getenv("MIN_NAME_LENGTH", "2"))
MAX_TRACKED_TOKENS = int(os.getenv("MAX_TRACKED_TOKENS", "300"))

# State
TRACKED = {}
RECENT_MINTS = deque(maxlen=5000)


# ---------------------------
# TELEGRAM ALERT
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
        except Exception as e:
            print(f"Telegram error: {e}")
    print(msg)


# ---------------------------
# HELPERS
# ---------------------------
def now_ts() -> float:
    return time.time()


def safe_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def get_first(d: dict, keys: list[str], default=None):
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def md_escape(text: str) -> str:
    if text is None:
        return ""
    for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(ch, f"\\{ch}")
    return text


def short_addr(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr or "unknown"
    return f"{addr[:4]}...{addr[-4:]}"


def dexscreener_link(mint: str) -> str:
    return f"https://dexscreener.com/solana/{mint}"


def gmgn_link(mint: str) -> str:
    return f"https://gmgn.ai/sol/token/{mint}"


# ---------------------------
# TOKEN STATE
# ---------------------------
def new_token_state(payload: dict) -> dict:
    mint = get_first(payload, ["mint", "tokenAddress", "address"])
    creator = get_first(payload, ["creator", "deployer", "owner"])
    name = get_first(payload, ["name", "tokenName"], "Unknown")
    symbol = get_first(payload, ["symbol", "ticker", "tokenSymbol"], "Unknown")

    created_at = now_ts()

    return {
        "mint": mint,
        "creator": creator,
        "name": str(name).strip() if name else "Unknown",
        "symbol": str(symbol).strip() if symbol else "Unknown",
        "created_at": created_at,
        "last_seen": created_at,
        "buys": 0,
        "sells": 0,
        "buy_sol": 0.0,
        "sell_sol": 0.0,
        "unique_buyers": set(),
        "unique_sellers": set(),
        "wallet_buy_count": defaultdict(int),
        "wallet_sell_count": defaultdict(int),
        "scout_sent": False,
        "hot_sent": False,
        "last_market_cap": 0.0,
        "last_price": 0.0,
        "events": [],
    }


def track_token(payload: dict) -> None:
    mint = get_first(payload, ["mint", "tokenAddress", "address"])
    if not mint or mint in TRACKED:
        return

    if len(TRACKED) >= MAX_TRACKED_TOKENS:
        oldest_mint = min(TRACKED, key=lambda m: TRACKED[m]["created_at"])
        TRACKED.pop(oldest_mint, None)

    state = new_token_state(payload)
    TRACKED[mint] = state
    RECENT_MINTS.append(mint)
    send_scout_alert(state)


# ---------------------------
# ALERTS
# ---------------------------
def send_scout_alert(state: dict) -> None:
    age = int(now_ts() - state["created_at"])
    if age < SCOUT_MIN_AGE_SECONDS or state["scout_sent"]:
        return

    state["scout_sent"] = True
    name = md_escape(state["name"])
    symbol = md_escape(state["symbol"])
    mint = state["mint"]
    creator = md_escape(short_addr(state["creator"]))

    msg = (
        f"🚨 *PUMPFUN SCOUT*\n\n"
        f"*{name}* \$begin:math:text$\{symbol\}\\$end:math:text$\n"
        f"Mint: `{mint}`\n"
        f"Creator: `{creator}`\n"
        f"Age: {age}s\n\n"
        f"DexScreener\n{dexscreener_link(mint)}\n\n"
        f"GMGN\n{gmgn_link(mint)}"
    )
    send(msg)


def send_hot_alert(state: dict, score: int, reason: str) -> None:
    if state["hot_sent"]:
        return
    state["hot_sent"] = True

    name = md_escape(state["name"])
    symbol = md_escape(state["symbol"])
    mint = state["mint"]
    age = int(now_ts() - state["created_at"])
    market_cap = state["last_market_cap"]
    price = state["last_price"]
    buys = state["buys"]
    sells = state["sells"]
    unique_buyers = len(state["unique_buyers"])
    buy_sell_ratio = (buys / sells) if sells > 0 else buys

    msg = (
        f"🔥 *PUMPFUN HOT ALERT*\n\n"
        f"*{name}* \$begin:math:text$\{symbol\}\\$end:math:text$\n"
        f"Score: *{score}/100*\n"
        f"Reason: {md_escape(reason)}\n\n"
        f"Mint: `{mint}`\n"
        f"Age: {age}s\n"
        f"Buys: {buys}\n"
        f"Sells: {sells}\n"
        f"Unique buyers: {unique_buyers}\n"
        f"Buy volume: {state['buy_sol']:.2f} SOL\n"
        f"Sell volume: {state['sell_sol']:.2f} SOL\n"
        f"Buy/Sell ratio: {buy_sell_ratio:.2f}x\n"
        f"Market cap: ${market_cap:,.0f}\n"
        f"Price: ${price:.10f}\n"
        f"One successful sell: {'YES' if sells >= 1 else 'NO'}\n\n"
        f"DexScreener\n{dexscreener_link(mint)}\n\n"
        f"GMGN\n{gmgn_link(mint)}"
    )
    send(msg)


# ---------------------------
# SCORING
# ---------------------------
def score_token(state: dict) -> tuple[int, str]:
    buys = state["buys"]
    sells = state["sells"]
    unique_buyers = len(state["unique_buyers"])
    buy_sol = state["buy_sol"]
    market_cap = state["last_market_cap"]
    age = now_ts() - state["created_at"]

    if age > TRACK_MAX_SECONDS:
        return 0, "expired"

    if REQUIRE_ONE_SUCCESSFUL_SELL and sells < 1:
        return 0, "no successful sell yet"

    if buys < MIN_TOTAL_BUYS:
        return 0, "not enough buys"

    if unique_buyers < MIN_UNIQUE_BUYERS:
        return 0, "not enough unique buyers"

    if buy_sol < MIN_BUY_SOL:
        return 0, "buy volume too low"

    if market_cap and market_cap < MIN_MARKET_CAP_USD:
        return 0, "market cap too low"

    if market_cap and market_cap > MAX_MARKET_CAP_USD:
        return 0, "market cap too high"

    buy_sell_ratio = (buys / sells) if sells > 0 else buys
    if buy_sell_ratio < MIN_BUY_SELL_RATIO:
        return 0, "buy/sell ratio too weak"

    buyer_velocity = unique_buyers / max(age, 1)
    volume_velocity = buy_sol / max(age, 1)

    score = 0
    score += min(30, int(unique_buyers * 2.5))
    score += min(25, int(buy_sol * 3))
    score += min(20, int(buy_sell_ratio * 5))
    score += min(15, int(buyer_velocity * 60))
    score += min(10, int(volume_velocity * 50))
    score = min(100, score)

    reason = f"{buys} buys, {sells} sells, {unique_buyers} unique buyers, {buy_sol:.2f} SOL bought"
    return score, reason


# ---------------------------
# MESSAGE PARSING
# ---------------------------
def normalize_trade_side(payload: dict) -> str:
    side = str(get_first(payload, ["txType", "type", "side", "action"], "")).lower()
    if "buy" in side:
        return "buy"
    if "sell" in side:
        return "sell"
    return ""


def extract_wallet(payload: dict) -> str:
    return str(get_first(payload, ["traderPublicKey", "wallet", "user", "maker", "owner"], ""))


def extract_sol_amount(payload: dict) -> float:
    return safe_float(get_first(payload, ["solAmount", "amountSol", "amount_in_sol", "volumeSol", "vSolInBondingCurve"]))


def extract_market_cap(payload: dict) -> float:
    return safe_float(get_first(payload, ["marketCapUsd", "usdMarketCap", "marketCap", "mc"]))


def extract_price(payload: dict) -> float:
    return safe_float(get_first(payload, ["priceUsd", "usdPrice", "price", "tokenPriceUsd"]))


def should_track_creation(payload: dict) -> bool:
    mint = get_first(payload, ["mint", "tokenAddress", "address"])
    name = str(get_first(payload, ["name", "tokenName"], "")).strip()
    symbol = str(get_first(payload, ["symbol", "ticker", "tokenSymbol"], "")).strip()

    if not mint:
        return False
    if len(name) < MIN_NAME_LENGTH and len(symbol) < MIN_NAME_LENGTH:
        return False
    return True


# ---------------------------
# EVENT HANDLING
# ---------------------------
def handle_create_event(payload: dict) -> None:
    if should_track_creation(payload):
        track_token(payload)


def handle_trade_event(payload: dict) -> None:
    mint = get_first(payload, ["mint", "tokenAddress", "address"])
    if not mint or mint not in TRACKED:
        return

    state = TRACKED[mint]
    state["last_seen"] = now_ts()

    wallet = extract_wallet(payload)
    side = normalize_trade_side(payload)
    sol_amount = extract_sol_amount(payload)
    market_cap = extract_market_cap(payload)
    price = extract_price(payload)

    if market_cap > 0:
        state["last_market_cap"] = market_cap
    if price > 0:
        state["last_price"] = price

    if side == "buy":
        state["buys"] += 1
        state["buy_sol"] += sol_amount
        if wallet:
            state["unique_buyers"].add(wallet)
            state["wallet_buy_count"][wallet] += 1
    elif side == "sell":
        state["sells"] += 1
        state["sell_sol"] += sol_amount
        if wallet:
            state["unique_sellers"].add(wallet)
            state["wallet_sell_count"][wallet] += 1
    else:
        return

    state["events"].append(
        {
            "ts": now_ts(),
            "wallet": wallet,
            "side": side,
            "sol": sol_amount,
            "market_cap": market_cap,
            "price": price,
        }
    )

    score, reason = score_token(state)
    if score >= 70 and (now_ts() - state["created_at"]) <= HOT_WINDOW_SECONDS:
        send_hot_alert(state, score, reason)


# ---------------------------
# CLEANUP
# ---------------------------
async def cleanup_loop() -> None:
    while True:
        try:
            cutoff = now_ts() - TRACK_MAX_SECONDS
            stale = [mint for mint, state in TRACKED.items() if state["last_seen"] < cutoff]
            for mint in stale:
                TRACKED.pop(mint, None)
        except Exception as e:
            print(f"Cleanup error: {e}")
        await asyncio.sleep(15)


# ---------------------------
# WEBSOCKET LISTENER
# ---------------------------
async def maybe_subscribe_new_tracked_mint(ws, mint: str) -> None:
    try:
        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
    except Exception as e:
        print(f"Subscribe trade error for {mint}: {e}")


async def listen() -> None:
    backoff = 2
    while True:
        try:
            print(f"Connecting to {PUMPPORTAL_WS}...")
            async with websockets.connect(PUMPPORTAL_WS, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
                print("Connected to PumpPortal websocket")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))

                known = set(TRACKED.keys())
                for mint in known:
                    await maybe_subscribe_new_tracked_mint(ws, mint)

                backoff = 2

                while True:
                    raw = await ws.recv()
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        continue

                    mint_before = set(TRACKED.keys())
                    if get_first(payload, ["mint", "tokenAddress", "address"]) and any(
                        k in payload for k in ["name", "symbol", "ticker", "tokenName"]
                    ):
                        handle_create_event(payload)

                    new_mints = set(TRACKED.keys()) - mint_before
                    for mint in new_mints:
                        await maybe_subscribe_new_tracked_mint(ws, mint)

                    side = normalize_trade_side(payload)
                    if side in {"buy", "sell"}:
                        handle_trade_event(payload)

        except Exception as e:
            print(f"Websocket error: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


# ---------------------------
# MAIN
# ---------------------------
async def main() -> None:
    send("🚀 Pump.fun early scanner started")
    await asyncio.gather(
        listen(),
        cleanup_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
