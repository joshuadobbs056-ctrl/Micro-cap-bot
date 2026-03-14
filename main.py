import subprocess
import sys


def install(package: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


try:
    import websockets
except ImportError:
    install("websockets")
    import websockets

try:
    import requests
except ImportError:
    install("requests")
    import requests

import asyncio
import json
import os
import time

# ---------------------------
# CONFIG
# ---------------------------
PUMP_WS = os.getenv("PUMP_WS", "wss://pumpportal.fun/api/data")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MIN_BUYS = int(os.getenv("MIN_BUYS", "8"))
MIN_UNIQUE_BUYERS = int(os.getenv("MIN_UNIQUE_BUYERS", "6"))
MIN_SOL_VOLUME = float(os.getenv("MIN_SOL_VOLUME", "3"))

TOKENS = {}


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
            print("Telegram error:", e)

    print(msg)


# ---------------------------
# HELPERS
# ---------------------------
def to_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def get_mint(data: dict) -> str:
    return data.get("mint") or data.get("tokenAddress") or data.get("address") or ""


def get_name(data: dict) -> str:
    return str(data.get("name") or data.get("tokenName") or "Unknown")


def get_symbol(data: dict) -> str:
    return str(data.get("symbol") or data.get("ticker") or data.get("tokenSymbol") or "?")


def get_wallet(data: dict) -> str:
    return str(
        data.get("traderPublicKey")
        or data.get("wallet")
        or data.get("user")
        or data.get("maker")
        or ""
    )


def is_buy(data: dict) -> bool:
    tx_type = str(data.get("txType") or data.get("type") or data.get("side") or "").lower()
    return "buy" in tx_type


# ---------------------------
# NEW TOKEN
# ---------------------------
def new_token(data: dict) -> None:
    mint = get_mint(data)

    if not mint or mint in TOKENS:
        return

    name = get_name(data)
    symbol = get_symbol(data)

    TOKENS[mint] = {
        "name": name,
        "symbol": symbol,
        "created": time.time(),
        "buys": 0,
        "buy_sol": 0.0,
        "buyers": set(),
        "alerted": False,
    }

    send(
        f"🟡 *New pump.fun token*\n\n"
        f"*{name} ({symbol})*\n"
        f"`{mint}`"
    )


# ---------------------------
# TRADE EVENT
# ---------------------------
def trade_event(data: dict) -> None:
    mint = get_mint(data)

    if mint not in TOKENS:
        return

    if not is_buy(data):
        return

    token = TOKENS[mint]
    wallet = get_wallet(data)
    sol = to_float(data.get("solAmount") or data.get("amountSol") or data.get("volumeSol"), 0.0)

    token["buys"] += 1
    token["buy_sol"] += sol

    if wallet:
        token["buyers"].add(wallet)

    buyers = len(token["buyers"])

    if not token["alerted"]:
        if (
            token["buys"] >= MIN_BUYS
            and buyers >= MIN_UNIQUE_BUYERS
            and token["buy_sol"] >= MIN_SOL_VOLUME
        ):
            token["alerted"] = True

            name = token["name"]
            symbol = token["symbol"]

            msg = (
                f"🚨 *PUMPFUN MOMENTUM*\n\n"
                f"*{name} ({symbol})*\n\n"
                f"Buys: {token['buys']}\n"
                f"Buy volume: {token['buy_sol']:.2f} SOL\n"
                f"Unique buyers: {buyers}\n\n"
                f"DexScreener\n"
                f"https://dexscreener.com/solana/{mint}\n\n"
                f"GMGN\n"
                f"https://gmgn.ai/sol/token/{mint}"
            )

            send(msg)


# ---------------------------
# CLEANUP
# ---------------------------
async def cleanup_loop() -> None:
    while True:
        try:
            now = time.time()
            stale = []

            for mint, token in TOKENS.items():
                if now - token["created"] > 1800:
                    stale.append(mint)

            for mint in stale:
                TOKENS.pop(mint, None)

        except Exception as e:
            print("Cleanup error:", e)

        await asyncio.sleep(60)


# ---------------------------
# WEBSOCKET LISTENER
# ---------------------------
async def listen() -> None:
    while True:
        try:
            async with websockets.connect(PUMP_WS, ping_interval=20, ping_timeout=20) as ws:
                send("🚀 Pump.fun scanner started")

                await ws.send(json.dumps({"method": "subscribeNewToken"}))

                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)

                    if not isinstance(data, dict):
                        continue

                    mint_before = set(TOKENS.keys())

                    if get_mint(data) and ("name" in data or "symbol" in data or "tokenName" in data):
                        new_token(data)

                    new_mints = set(TOKENS.keys()) - mint_before
                    for mint in new_mints:
                        try:
                            await ws.send(
                                json.dumps(
                                    {
                                        "method": "subscribeTokenTrade",
                                        "keys": [mint],
                                    }
                                )
                            )
                        except Exception as e:
                            print(f"Trade subscribe error for {mint}: {e}")

                    if "txType" in data or "type" in data or "side" in data:
                        trade_event(data)

        except Exception as e:
            print("Websocket error:", e)
            await asyncio.sleep(3)


# ---------------------------
# MAIN
# ---------------------------
async def main() -> None:
    await asyncio.gather(
        listen(),
        cleanup_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
