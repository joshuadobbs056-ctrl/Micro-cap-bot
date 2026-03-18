import os
import time
import requests
import threading
from datetime import datetime, timezone
from typing import List, Dict, Optional

# ================= CONFIG =================

SESSION = requests.Session()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

FUTURES_PRODUCTS = os.getenv(
    "FUTURES_PRODUCTS",
    "BTC-PERP-INTX,ETH-PERP-INTX,SOL-PERP-INTX"
).split(",")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
ACCOUNT_UPDATE_INTERVAL = int(os.getenv("ACCOUNT_UPDATE_INTERVAL", "60"))

START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))

FAST_EMA = 20
SLOW_EMA = 50
ENTRY_EMA = 9

STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.06

TRAILING_STOP_PCT = 0.015
TRAILING_ACTIVATE = 0.01

RISK_PER_TRADE = 0.1

PRODUCT_URL = "https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}"
CANDLE_URL = "https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles"

# ================= UTILS =================

def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(msg)
        return

    try:
        r = SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000]},
            timeout=10
        )
        if r.status_code != 200:
            print("Telegram error:", r.text)
    except Exception as e:
        print("Telegram fail:", e)

def log(msg, tg=False):
    print(msg)
    if tg:
        send_telegram(msg)

# ================= DATA =================

def get_price(product):
    try:
        r = SESSION.get(PRODUCT_URL.format(product_id=product), timeout=10)
        return float(r.json()["price"])
    except:
        return None

def get_candles(product, granularity="ONE_HOUR"):
    try:
        now = int(time.time())
        start = now - 3600 * 200

        r = SESSION.get(
            CANDLE_URL.format(product_id=product),
            params={"start": start, "end": now, "granularity": granularity},
            timeout=10
        )
        return r.json().get("candles", [])
    except:
        return []

# ================= INDICATORS =================

def ema(data, period):
    k = 2 / (period + 1)
    val = data[0]
    out = []

    for price in data:
        val = price * k + val * (1 - k)
        out.append(val)
    return out

def trend_signal(candles):
    closes = [float(c["close"]) for c in candles]

    fast = ema(closes, FAST_EMA)
    slow = ema(closes, SLOW_EMA)

    if fast[-1] > slow[-1]:
        return "long"
    elif fast[-1] < slow[-1]:
        return "short"
    return None

def entry_signal(candles, trend):
    closes = [float(c["close"]) for c in candles]
    line = ema(closes, ENTRY_EMA)

    if trend == "long":
        if closes[-1] > line[-1] and closes[-2] < line[-2]:
            return "long"

    if trend == "short":
        if closes[-1] < line[-1] and closes[-2] > line[-2]:
            return "short"

    return None

# ================= PORTFOLIO =================

def portfolio():
    return {
        "cash": START_BALANCE,
        "start": START_BALANCE,
        "trades": [],
        "closed": []
    }

def portfolio_value(p):
    total = p["cash"]
    for t in p["trades"]:
        total += t["current_value"]
    return total

# ================= TRADING =================

def open_trade(p, product, side, price):
    risk_amount = p["cash"] * RISK_PER_TRADE
    qty = risk_amount / price

    value = qty * price

    p["cash"] -= value

    trade = {
        "product": product,
        "side": side,
        "entry": price,
        "price": price,
        "qty": qty,
        "entry_value": value,
        "current_value": value,
        "peak": 0,
        "trail": None,
        "active": False
    }

    p["trades"].append(trade)

    send_telegram(f"🟢 OPEN {side.upper()} {product} @ {price}")

def close_trade(p, t, price, reason):
    if t["side"] == "long":
        value = t["qty"] * price
        pnl = value - t["entry_value"]
    else:
        pnl = (t["entry"] - price) * t["qty"]
        value = t["entry_value"] + pnl

    p["cash"] += value
    p["trades"].remove(t)
    p["closed"].append(t)

    send_telegram(
        f"🔴 CLOSE {t['product']} | PnL ${pnl:.2f} | {reason}"
    )

def manage_trades(p):
    for t in list(p["trades"]):
        price = get_price(t["product"])
        if not price:
            continue

        t["price"] = price

        if t["side"] == "long":
            t["current_value"] = t["qty"] * price
            pnl = t["current_value"] - t["entry_value"]
        else:
            pnl = (t["entry"] - price) * t["qty"]
            t["current_value"] = t["entry_value"] + pnl

        if pnl > t["peak"]:
            t["peak"] = pnl

        # SL
        if price <= t["entry"] * (1 - STOP_LOSS_PCT):
            close_trade(p, t, price, "SL")
            continue

        # TP
        if price >= t["entry"] * (1 + TAKE_PROFIT_PCT):
            close_trade(p, t, price, "TP")
            continue

        # Trailing
        if price >= t["entry"] * (1 + TRAILING_ACTIVATE):
            t["active"] = True
            new_trail = price * (1 - TRAILING_STOP_PCT)

            if not t["trail"] or new_trail > t["trail"]:
                t["trail"] = new_trail

        if t["active"] and price <= t["trail"]:
            close_trade(p, t, price, "TRAIL")

# ================= ACCOUNT =================

def account_update(p):
    total = portfolio_value(p)
    pnl = total - p["start"]
    pct = (pnl / p["start"]) * 100

    msg = (
        "📊 ACCOUNT UPDATE\n\n"
        f"Value: ${total:.2f}\n"
        f"Cash: ${p['cash']:.2f}\n"
        f"PnL: ${pnl:.2f} ({pct:.2f}%)\n"
        f"Trades: {len(p['trades'])}"
    )

    send_telegram(msg)

def account_loop(p):
    while True:
        account_update(p)
        time.sleep(ACCOUNT_UPDATE_INTERVAL)

# ================= MAIN =================

def main():
    p = portfolio()

    send_telegram("🚀 BOT STARTED")

    threading.Thread(target=account_loop, args=(p,), daemon=True).start()

    while True:
        try:
            manage_trades(p)

            for product in FUTURES_PRODUCTS:
                if len(p["trades"]) >= MAX_OPEN_TRADES:
                    break

                candles = get_candles(product)
                if not candles:
                    continue

                trend = trend_signal(candles)
                entry = entry_signal(candles, trend)

                if entry:
                    price = get_price(product)
                    if price:
                        open_trade(p, product, entry, price)

            log(
                f"Scan complete | Value ${portfolio_value(p):.2f} | Cash ${p['cash']:.2f} | Trades {len(p['trades'])}",
                tg=True
            )

        except Exception as e:
            send_telegram(f"ERROR: {e}")

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
