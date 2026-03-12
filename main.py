# Coin Sniper — Savage ELITE (PAPER) — FLASH CRASH HUNTER (OPTIMIZED)
# 🎯 Goal: Catch Flash Crashes and sell the bounce
# ⚡ Faster crash detection
# ⚡ Better trigger thresholds
# ⚡ Full Telegram reporting

import os, time, json, requests
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Tuple

# =========================
# CONFIG
# =========================

START_BALANCE = float(os.getenv("START_BALANCE", "2000"))

SCAN_INTERVAL = 2
STATUS_INTERVAL = 60

# Entry Triggers (optimized)
CRASH_THRESHOLD_PCT = 5.0
VOL_SPIKE_RATIO = 2.0
RSI_BUY_LEVEL = 25

# Exit Triggers
RECOVERY_TARGET_PCT = 3.5
STOP_LOSS_PCT = 5.0

MAX_OPEN_TRADES = 5

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

COINBASE_API = "https://api.exchange.coinbase.com"

# =========================
# POSITION STRUCTURE
# =========================

@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_time: float

# =========================
# NOTIFY
# =========================

def notify(msg: str):
    print(msg, flush=True)

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

            requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": msg
                },
                timeout=10
            )
        except:
            pass

# =========================
# API CHECK
# =========================

def check_api_connection():

    try:
        r = requests.get(
            f"{COINBASE_API}/products/BTC-USD/ticker",
            timeout=5
        )

        return r.status_code == 200

    except:
        return False

# =========================
# GET CANDLES
# =========================

def get_candles(product_id: str):

    try:
        r = requests.get(
            f"{COINBASE_API}/products/{product_id}/candles",
            params={"granularity": 60},
            timeout=5
        )

        if r.status_code == 200:
            return r.json()[:60]

    except:
        pass

    return []

# =========================
# RSI
# =========================

def calculate_rsi(prices, period=14):

    if len(prices) < period + 1:
        return 50

    deltas = np.diff(prices)

    gain = np.where(deltas > 0, deltas, 0)
    loss = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gain[-period:])
    avg_loss = np.mean(loss[-period:])

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi

# =========================
# FLASH CRASH DETECTION
# =========================

def detect_flash_crash(sym: str, candles: List[list]) -> Tuple[bool, str]:

    if len(candles) < 20:
        return False, ""

    closes = [float(c[4]) for c in reversed(candles)]
    vols = [float(c[5]) for c in reversed(candles)]

    current = closes[-1]
    prev = closes[-2]

    drop_pct = ((prev - current) / prev) * 100

    avg_vol = np.mean(vols[-15:-1])

    rsi = calculate_rsi(closes)

    if drop_pct >= CRASH_THRESHOLD_PCT:

        if vols[-1] > avg_vol * VOL_SPIKE_RATIO:

            if rsi <= RSI_BUY_LEVEL:

                return True, f"Drop:{drop_pct:.2f}% RSI:{rsi:.1f}"

    return False, ""

# =========================
# STATUS REPORT
# =========================

def status_report(state, positions, last_prices, api_ok):

    conn = "🟢" if api_ok else "🔴"

    cash = state["cash"]
    realized = state["realized_pnl"]

    wins = state["wins"]
    losses = state["losses"]

    equity = cash

    for sym, pos in positions.items():

        px = last_prices.get(sym, pos.entry_price)

        equity += px * pos.qty

    msg = (
        f"📊 FLASH HUNTER REPORT\n"
        f"API Status: {conn} Connected\n"
        f"Equity: ${equity:.2f} | Cash: ${cash:.2f}\n"
        f"Realized PnL: ${realized:.2f}\n"
        f"W/L: {wins}/{losses} | Open: {len(positions)}/{MAX_OPEN_TRADES}"
    )

    notify(msg)

# =========================
# MAIN
# =========================

def main():

    state = {
        "cash": START_BALANCE,
        "wins": 0,
        "losses": 0,
        "realized_pnl": 0
    }

    positions: Dict[str, Position] = {}

    last_status = 0

    notify("🌪 FLASH HUNTER STARTED")

    try:

        products = requests.get(f"{COINBASE_API}/products").json()

        universe = [
            p["id"]
            for p in products
            if p["quote_currency"] == "USD"
            and p["status"] == "online"
        ]

    except:

        universe = []

    notify(f"Scanning {len(universe)} coins")

    while True:

        try:

            api_ok = check_api_connection()

            current_prices = {}

            # =====================
            # MANAGE TRADES
            # =====================

            for sym, pos in list(positions.items()):

                candles = get_candles(sym)

                if not candles:
                    continue

                px = float(candles[0][4])

                current_prices[sym] = px

                pnl_pct = (px / pos.entry_price - 1) * 100

                if pnl_pct >= RECOVERY_TARGET_PCT:

                    pnl = (px - pos.entry_price) * pos.qty

                    state["cash"] += px * pos.qty
                    state["realized_pnl"] += pnl
                    state["wins"] += 1

                    positions.pop(sym)

                    notify(
                        f"💰 PROFIT EXIT {sym} "
                        f"+{pnl_pct:.2f}% "
                        f"+${pnl:.2f}"
                    )

                elif pnl_pct <= -STOP_LOSS_PCT:

                    pnl = (px - pos.entry_price) * pos.qty

                    state["cash"] += px * pos.qty
                    state["realized_pnl"] += pnl
                    state["losses"] += 1

                    positions.pop(sym)

                    notify(
                        f"❌ STOP LOSS {sym} "
                        f"{pnl_pct:.2f}% "
                        f"${pnl:.2f}"
                    )

            # =====================
            # FIND NEW TRADES
            # =====================

            if api_ok and len(positions) < MAX_OPEN_TRADES:

                for sym in universe:

                    if sym in positions:
                        continue

                    candles = get_candles(sym)

                    crash, reason = detect_flash_crash(sym, candles)

                    if crash:

                        px = float(candles[0][4])

                        buy_amt = state["cash"] / (MAX_OPEN_TRADES - len(positions))

                        if buy_amt > 10:

                            qty = buy_amt / px

                            positions[sym] = Position(
                                sym,
                                qty,
                                px,
                                time.time()
                            )

                            state["cash"] -= buy_amt

                            notify(
                                f"🚨 SNIPED {sym}\n"
                                f"Entry: ${px:.4f}\n"
                                f"{reason}"
                            )

            # =====================
            # STATUS REPORT
            # =====================

            if time.time() - last_status >= STATUS_INTERVAL:

                status_report(state, positions, current_prices, api_ok)

                last_status = time.time()

            time.sleep(SCAN_INTERVAL)

        except Exception as e:

            notify(f"⚠️ BOT ERROR {str(e)}")

            time.sleep(5)

# =========================

if __name__ == "__main__":
    main()
