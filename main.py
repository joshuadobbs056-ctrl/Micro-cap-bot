import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict, Optional

# ================= CONFIG =================

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

FUTURES_PRODUCTS = [
    p.strip() for p in os.getenv(
        "FUTURES_PRODUCTS",
        "BTC-PERP-INTX,ETH-PERP-INTX,SOL-PERP-INTX"
    ).split(",") if p.strip()
]

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
ACCOUNT_UPDATE_INTERVAL = int(os.getenv("ACCOUNT_UPDATE_INTERVAL", "300"))
SCAN_STATUS_INTERVAL = int(os.getenv("SCAN_STATUS_INTERVAL", "300"))

START_BALANCE = float(os.getenv("START_BALANCE", "500"))
PURCHASE_AMOUNT_USD = float(os.getenv("PURCHASE_AMOUNT_USD", "200"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "2"))

FAST_EMA = int(os.getenv("FAST_EMA", "20"))
SLOW_EMA = int(os.getenv("SLOW_EMA", "50"))
ENTRY_EMA = int(os.getenv("ENTRY_EMA", "9"))

STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.02"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.05"))

TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.015"))
TRAILING_ACTIVATE = float(os.getenv("TRAILING_ACTIVATE", "0.01"))

BREAKEVEN_TRIGGER = float(os.getenv("BREAKEVEN_TRIGGER", "0.01"))

TREND_GRANULARITY = os.getenv("TREND_GRANULARITY", "ONE_HOUR").strip()
TREND_CANDLE_LIMIT = int(os.getenv("TREND_CANDLE_LIMIT", "200"))

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
        SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000]},
            timeout=15
        )
    except Exception as e:
        print("Telegram fail:", e)


# ================= DATA =================

def get_price(product):
    try:
        r = SESSION.get(PRODUCT_URL.format(product_id=product), timeout=10)
        return float(r.json().get("price"))
    except:
        return None


def get_candles(product, granularity, limit):
    try:
        now = int(time.time())
        seconds_map = {
            "ONE_MINUTE": 60,
            "FIVE_MINUTE": 300,
            "FIFTEEN_MINUTE": 900,
            "ONE_HOUR": 3600,
        }
        step = seconds_map.get(granularity, 3600)
        start = now - (step * limit)

        r = SESSION.get(
            CANDLE_URL.format(product_id=product),
            params={"start": start, "end": now, "granularity": granularity},
            timeout=10
        )
        data = r.json().get("candles", [])
        return sorted(data, key=lambda x: int(x["start"]))
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


def trend_signal(c):
    closes = [float(x["close"]) for x in c]
    fast = ema(closes, FAST_EMA)
    slow = ema(closes, SLOW_EMA)

    if fast[-1] > slow[-1]:
        return "long"
    if fast[-1] < slow[-1]:
        return "short"
    return None


def entry_signal(c, trend):
    closes = [float(x["close"]) for x in c]
    line = ema(closes, ENTRY_EMA)

    if trend == "long":
        if closes[-1] > line[-1] and closes[-2] <= line[-2]:
            return "long"

    if trend == "short":
        if closes[-1] < line[-1] and closes[-2] >= line[-2]:
            return "short"

    return None


# ================= PORTFOLIO =================

def portfolio():
    return {
        "cash": START_BALANCE,
        "reserved_profit": 0.0,
        "start": START_BALANCE,
        "trades": [],
        "closed": []
    }


def tradable_cash(p):
    return max(0, p["cash"] - p["reserved_profit"])


# ================= TRADING =================

def open_trade(p, product, side, price):
    if tradable_cash(p) < PURCHASE_AMOUNT_USD:
        return

    value = PURCHASE_AMOUNT_USD
    qty = value / price

    p["cash"] -= value

    trade = {
        "product": product,
        "side": side,
        "entry": price,
        "qty": qty,
        "entry_value": value,
        "peak": 0,
        "trail": None,
        "active": False,
        "breakeven": False
    }

    p["trades"].append(trade)

    send_telegram(f"🟢 OPEN {side.upper()} {product} @ ${price:.2f}")


def close_trade(p, t, price, reason):
    if t["side"] == "long":
        value = t["qty"] * price
        pnl = value - t["entry_value"]
    else:
        pnl = (t["entry"] - price) * t["qty"]
        value = t["entry_value"] + pnl

    p["cash"] += value
    p["trades"].remove(t)

    # 🔒 LOCK PROFITS
    if p["cash"] > p["start"]:
        p["reserved_profit"] = p["cash"] - p["start"]

    send_telegram(
        f"🔴 CLOSE {t['product']} {t['side']}\n"
        f"PnL: ${pnl:.2f}\n"
        f"Reason: {reason}\n"
        f"Locked Profit: ${p['reserved_profit']:.2f}"
    )


def manage_trades(p):
    for t in list(p["trades"]):
        price = get_price(t["product"])
        if not price:
            continue

        if t["side"] == "long":
            pnl_pct = (price - t["entry"]) / t["entry"]

            # breakeven
            if pnl_pct >= BREAKEVEN_TRIGGER and not t["breakeven"]:
                t["trail"] = t["entry"]
                t["breakeven"] = True

            # trailing
            if pnl_pct >= TRAILING_ACTIVATE:
                t["active"] = True
                new_trail = price * (1 - TRAILING_STOP_PCT)
                if not t["trail"] or new_trail > t["trail"]:
                    t["trail"] = new_trail

            # exits
            if price <= t["entry"] * (1 - STOP_LOSS_PCT):
                close_trade(p, t, price, "SL")
                continue

            if t["active"] and price <= t["trail"]:
                close_trade(p, t, price, "TRAIL")
                continue

        else:
            pnl_pct = (t["entry"] - price) / t["entry"]

            if pnl_pct >= BREAKEVEN_TRIGGER and not t["breakeven"]:
                t["trail"] = t["entry"]
                t["breakeven"] = True

            if pnl_pct >= TRAILING_ACTIVATE:
                t["active"] = True
                new_trail = price * (1 + TRAILING_STOP_PCT)
                if not t["trail"] or new_trail < t["trail"]:
                    t["trail"] = new_trail

            if price >= t["entry"] * (1 + STOP_LOSS_PCT):
                close_trade(p, t, price, "SL")
                continue

            if t["active"] and price >= t["trail"]:
                close_trade(p, t, price, "TRAIL")
                continue


# ================= MAIN =================

def main():
    p = portfolio()

    send_telegram("🚀 BOT STARTED (PROFIT LOCK ENABLED)")

    while True:
        try:
            manage_trades(p)

            for product in FUTURES_PRODUCTS:
                if len(p["trades"]) >= MAX_OPEN_TRADES:
                    break

                candles = get_candles(product, TREND_GRANULARITY, TREND_CANDLE_LIMIT)
                if not candles:
                    continue

                trend = trend_signal(candles)
                entry = entry_signal(candles, trend)

                if entry:
                    price = get_price(product)
                    if price:
                        open_trade(p, product, entry, price)

        except Exception as e:
            send_telegram(f"ERROR: {e}")

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
