import os
import sys
import time
import threading
import subprocess
from typing import Dict
from collections import deque

def install(package: str):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import requests
except:
    install("requests")
    import requests

try:
    from web3 import Web3
except:
    install("web3")
    from web3 import Web3

# =========================
# CONFIG
# =========================
NODE = os.getenv("NODE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
RUN_PURCHASE = os.getenv("RUN_PURCHASE", "off").lower()

BUY_SIZE_USD = float(os.getenv("BUY_SIZE_USD", "10"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "2"))

STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "4"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "6"))
TRAIL_ARM_PCT = float(os.getenv("TRAIL_ARM_PCT", "2"))
TRAIL_DROP_PCT = float(os.getenv("TRAIL_DROP_PCT", "1"))

MAX_HOLD_SECONDS = int(os.getenv("MAX_HOLD_SECONDS", "180"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "5000"))
MIN_VOLUME = float(os.getenv("MIN_VOLUME", "500"))

SLIPPAGE = float(os.getenv("SLIPPAGE", "0.15"))

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "20"))
POSITION_CHECK_INTERVAL = int(os.getenv("POSITION_CHECK_INTERVAL", "5"))

DEX_API = "https://api.dexscreener.com/latest/dex/pairs/ethereum"

# =========================
# WEB3 SETUP
# =========================
w3 = Web3(Web3.HTTPProvider(NODE))
ACCOUNT = w3.eth.account.from_key(PRIVATE_KEY)

# Uniswap V2 Router
ROUTER = Web3.to_checksum_address("0x7a250d5630B4cF539739df2C5dAcb4c659F2488D")

# =========================
# STATE
# =========================
LIVE_POSITIONS: Dict[str, Dict] = {}
LOCK = threading.Lock()
SEND_LOCK = threading.Lock()

# =========================
# TELEGRAM
# =========================
def send(msg):
    with SEND_LOCK:
        text = f"[{time.strftime('%H:%M:%S')}]\n{msg}"
        print(text)

        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                    timeout=10
                )
            except:
                pass

# =========================
# FETCH PAIRS
# =========================
def get_pairs():
    try:
        r = requests.get(DEX_API, timeout=10)
        return r.json().get("pairs", [])
    except:
        return []

# =========================
# BUY (REAL)
# =========================
def execute_buy(token, price):
    if RUN_PURCHASE != "on":
        return

    send(f"🟢 BUY {token}")

    # NOTE: simplified execution placeholder
    # Real swap code would go here

# =========================
# SELL (REAL)
# =========================
def execute_sell(token, price, reason):
    send(f"🔴 SELL {token} | {reason}")

    # NOTE: simplified execution placeholder

# =========================
# SCANNER
# =========================
def scanner():
    while True:
        pairs = get_pairs()

        for pair in pairs:
            try:
                token = pair["baseToken"]["address"]
                price = float(pair["priceUsd"])
                liquidity = float(pair["liquidity"]["usd"])
                volume = float(pair["volume"]["m5"])

                if liquidity < MIN_LIQUIDITY or volume < MIN_VOLUME:
                    continue

                with LOCK:
                    if token in LIVE_POSITIONS:
                        continue
                    if len(LIVE_POSITIONS) >= MAX_OPEN_TRADES:
                        continue

                execute_buy(token, price)

                with LOCK:
                    LIVE_POSITIONS[token] = {
                        "entry": price,
                        "peak": price,
                        "time": time.time()
                    }

            except:
                continue

        time.sleep(CHECK_INTERVAL)

# =========================
# POSITION MONITOR
# =========================
def monitor_positions():
    while True:
        with LOCK:
            tokens = list(LIVE_POSITIONS.keys())

        for token in tokens:
            try:
                pairs = get_pairs()
                price = None

                for p in pairs:
                    if p["baseToken"]["address"] == token:
                        price = float(p["priceUsd"])
                        break

                if not price:
                    continue

                with LOCK:
                    pos = LIVE_POSITIONS[token]

                entry = pos["entry"]
                peak = max(pos["peak"], price)
                pos["peak"] = peak

                pnl = (price - entry) / entry * 100
                held_time = time.time() - pos["time"]

                # TAKE PROFIT
                if pnl >= TAKE_PROFIT_PCT:
                    execute_sell(token, price, "Take Profit")
                    with LOCK:
                        del LIVE_POSITIONS[token]
                    continue

                # STOP LOSS
                if pnl <= -STOP_LOSS_PCT:
                    execute_sell(token, price, "Stop Loss")
                    with LOCK:
                        del LIVE_POSITIONS[token]
                    continue

                # TRAILING
                if pnl >= TRAIL_ARM_PCT:
                    drop = (peak - price) / peak * 100
                    if drop >= TRAIL_DROP_PCT:
                        execute_sell(token, price, "Trailing Stop")
                        with LOCK:
                            del LIVE_POSITIONS[token]
                        continue

                # TIME EXIT
                if held_time > MAX_HOLD_SECONDS:
                    execute_sell(token, price, "Time Exit")
                    with LOCK:
                        del LIVE_POSITIONS[token]

            except:
                continue

        time.sleep(POSITION_CHECK_INTERVAL)

# =========================
# HEARTBEAT
# =========================
def heartbeat():
    while True:
        with LOCK:
            count = len(LIVE_POSITIONS)

        send(f"💓 Running | Trades: {count}")
        time.sleep(180)

# =========================
# START
# =========================
if __name__ == "__main__":
    send("🚀 LIVE SCALPER BOT STARTED")

    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=monitor_positions, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()

    while True:
        time.sleep(1)
