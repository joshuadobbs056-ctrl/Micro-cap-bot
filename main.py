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

TREND_GRANULARITY = os.getenv("TREND_GRANULARITY", "ONE_HOUR").strip()
TREND_CANDLE_LIMIT = int(os.getenv("TREND_CANDLE_LIMIT", "200"))

PRODUCT_URL = "https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}"
CANDLE_URL = "https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles"

# ================= UTILS =================

def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def send_telegram(msg: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(msg)
        return

    try:
        r = SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000]},
            timeout=15
        )
        if r.status_code != 200:
            print("Telegram error:", r.text)
    except Exception as e:
        print("Telegram fail:", e)


def log(msg: str, tg: bool = False) -> None:
    stamped = f"[{utc()}] {msg}"
    print(stamped)
    if tg:
        send_telegram(stamped)


# ================= DATA =================

def get_price(product: str) -> Optional[float]:
    try:
        r = SESSION.get(PRODUCT_URL.format(product_id=product), timeout=15)
        r.raise_for_status()
        data = r.json()
        price = data.get("price")
        if price is None:
            return None
        return float(price)
    except Exception as e:
        print(f"get_price error for {product}: {e}")
        return None


def get_candles(product: str, granularity: str = "ONE_HOUR", limit: int = 200) -> List[Dict]:
    try:
        now = int(time.time())

        seconds_map = {
            "ONE_MINUTE": 60,
            "FIVE_MINUTE": 300,
            "FIFTEEN_MINUTE": 900,
            "THIRTY_MINUTE": 1800,
            "ONE_HOUR": 3600,
            "TWO_HOUR": 7200,
            "SIX_HOUR": 21600,
            "ONE_DAY": 86400,
        }

        step = seconds_map.get(granularity, 3600)
        start = now - (step * limit)

        r = SESSION.get(
            CANDLE_URL.format(product_id=product),
            params={"start": start, "end": now, "granularity": granularity},
            timeout=15
        )
        r.raise_for_status()

        data = r.json()
        candles = data.get("candles", [])
        candles = sorted(candles, key=lambda x: int(x["start"]))
        return candles
    except Exception as e:
        print(f"get_candles error for {product}: {e}")
        return []


# ================= INDICATORS =================

def ema(data: List[float], period: int) -> List[float]:
    if not data:
        return []
    if period <= 0:
        return data[:]

    k = 2 / (period + 1)
    val = data[0]
    out = []

    for price in data:
        val = price * k + val * (1 - k)
        out.append(val)

    return out


def trend_signal(candles: List[Dict]) -> Optional[str]:
    if len(candles) < max(FAST_EMA, SLOW_EMA) + 2:
        return None

    closes = [float(c["close"]) for c in candles]
    fast = ema(closes, FAST_EMA)
    slow = ema(closes, SLOW_EMA)

    if not fast or not slow:
        return None

    if fast[-1] > slow[-1]:
        return "long"
    if fast[-1] < slow[-1]:
        return "short"
    return None


def entry_signal(candles: List[Dict], trend: Optional[str]) -> Optional[str]:
    if trend is None or len(candles) < ENTRY_EMA + 3:
        return None

    closes = [float(c["close"]) for c in candles]
    line = ema(closes, ENTRY_EMA)

    if len(closes) < 2 or len(line) < 2:
        return None

    if trend == "long":
        if closes[-1] > line[-1] and closes[-2] <= line[-2]:
            return "long"

    if trend == "short":
        if closes[-1] < line[-1] and closes[-2] >= line[-2]:
            return "short"

    return None


# ================= PORTFOLIO =================

def portfolio() -> Dict:
    return {
        "cash": START_BALANCE,
        "start": START_BALANCE,
        "trades": [],
        "closed": []
    }


def portfolio_value(p: Dict) -> float:
    total = p["cash"]
    for t in p["trades"]:
        total += t["current_value"]
    return total


def has_open_trade_for_product(p: Dict, product: str) -> bool:
    return any(t["product"] == product for t in p["trades"])


# ================= TRADING =================

def open_trade(p: Dict, product: str, side: str, price: float) -> None:
    if has_open_trade_for_product(p, product):
        return

    value = min(PURCHASE_AMOUNT_USD, p["cash"])
    if value <= 0:
        return

    qty = value / price
    p["cash"] -= value

    trade = {
        "product": product,
        "side": side,
        "entry": price,
        "price": price,
        "qty": qty,
        "entry_value": value,
        "current_value": value,
        "peak": 0.0,
        "trail": None,
        "active": False,
        "opened_at": utc(),
    }

    p["trades"].append(trade)

    send_telegram(
        f"🟢 OPEN {side.upper()} {product}\n"
        f"Entry: ${price:.2f}\n"
        f"Size: ${value:.2f}\n"
        f"Qty: {qty:.8f}\n"
        f"Opened: {trade['opened_at']} UTC"
    )


def close_trade(p: Dict, t: Dict, price: float, reason: str) -> None:
    if t["side"] == "long":
        value = t["qty"] * price
        pnl = value - t["entry_value"]
    else:
        pnl = (t["entry"] - price) * t["qty"]
        value = t["entry_value"] + pnl

    t["exit"] = price
    t["exit_reason"] = reason
    t["realized_pnl"] = pnl
    t["closed_at"] = utc()

    p["cash"] += value
    p["trades"].remove(t)
    p["closed"].append(t)

    pnl_pct = (pnl / t["entry_value"]) * 100 if t["entry_value"] else 0.0

    send_telegram(
        f"🔴 CLOSE {t['product']} {t['side'].upper()}\n"
        f"Entry: ${t['entry']:.2f}\n"
        f"Exit: ${price:.2f}\n"
        f"PnL: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        f"Reason: {reason}\n"
        f"Closed: {t['closed_at']} UTC"
    )


def manage_trades(p: Dict) -> None:
    for t in list(p["trades"]):
        price = get_price(t["product"])
        if not price:
            continue

        t["price"] = price

        if t["side"] == "long":
            t["current_value"] = t["qty"] * price
            pnl = t["current_value"] - t["entry_value"]

            if pnl > t["peak"]:
                t["peak"] = pnl

            if price <= t["entry"] * (1 - STOP_LOSS_PCT):
                close_trade(p, t, price, "SL")
                continue

            if price >= t["entry"] * (1 + TAKE_PROFIT_PCT):
                close_trade(p, t, price, "TP")
                continue

            if price >= t["entry"] * (1 + TRAILING_ACTIVATE):
                t["active"] = True
                new_trail = price * (1 - TRAILING_STOP_PCT)
                if t["trail"] is None or new_trail > t["trail"]:
                    t["trail"] = new_trail

            if t["active"] and t["trail"] is not None and price <= t["trail"]:
                close_trade(p, t, price, "TRAIL")
                continue

        else:
            pnl = (t["entry"] - price) * t["qty"]
            t["current_value"] = t["entry_value"] + pnl

            if pnl > t["peak"]:
                t["peak"] = pnl

            if price >= t["entry"] * (1 + STOP_LOSS_PCT):
                close_trade(p, t, price, "SL")
                continue

            if price <= t["entry"] * (1 - TAKE_PROFIT_PCT):
                close_trade(p, t, price, "TP")
                continue

            if price <= t["entry"] * (1 - TRAILING_ACTIVATE):
                t["active"] = True
                new_trail = price * (1 + TRAILING_STOP_PCT)
                if t["trail"] is None or new_trail < t["trail"]:
                    t["trail"] = new_trail

            if t["active"] and t["trail"] is not None and price >= t["trail"]:
                close_trade(p, t, price, "TRAIL")
                continue


# ================= STATUS =================

def build_account_update(p: Dict) -> str:
    total = portfolio_value(p)
    pnl = total - p["start"]
    pct = (pnl / p["start"]) * 100 if p["start"] else 0.0

    lines = [
        "📊 ACCOUNT UPDATE",
        "",
        f"Value: ${total:.2f}",
        f"Cash: ${p['cash']:.2f}",
        f"PnL: ${pnl:.2f} ({pct:.2f}%)",
        f"Open Trades: {len(p['trades'])}",
        f"Closed Trades: {len(p['closed'])}",
    ]

    if p["trades"]:
        lines.append("")
        lines.append("Open Positions:")
        for t in p["trades"]:
            trade_pnl = t["current_value"] - t["entry_value"]
            trade_pct = (trade_pnl / t["entry_value"]) * 100 if t["entry_value"] else 0.0

            if t["side"] == "short":
                trade_pnl = (t["entry"] - t["price"]) * t["qty"]
                trade_pct = (trade_pnl / t["entry_value"]) * 100 if t["entry_value"] else 0.0

            lines.extend([
                "",
                f"{t['product']} | {t['side'].upper()}",
                f"Entry: ${t['entry']:.2f}",
                f"Current: ${t['price']:.2f}",
                f"Entry Value: ${t['entry_value']:.2f}",
                f"Current Value: ${t['current_value']:.2f}",
                f"PnL: ${trade_pnl:.2f} ({trade_pct:.2f}%)",
                f"Peak PnL: ${t['peak']:.2f}",
            ])
            if t["trail"] is not None:
                lines.append(f"Trail: ${t['trail']:.2f}")

    return "\n".join(lines)


def build_scan_status(p: Dict) -> str:
    total = portfolio_value(p)
    return (
        "🛰️ SCAN STATUS\n\n"
        f"Value: ${total:.2f}\n"
        f"Cash: ${p['cash']:.2f}\n"
        f"Open Trades: {len(p['trades'])}\n"
        f"Watching: {', '.join(FUTURES_PRODUCTS)}\n"
        f"Next scan in: {SCAN_INTERVAL}s"
    )


# ================= MAIN =================

def main() -> None:
    p = portfolio()

    send_telegram(
        "🚀 BOT STARTED\n"
        f"Start Balance: ${START_BALANCE:.2f}\n"
        f"Position Size: ${PURCHASE_AMOUNT_USD:.2f}\n"
        f"Max Open Trades: {MAX_OPEN_TRADES}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n"
        f"Account Update Interval: {ACCOUNT_UPDATE_INTERVAL}s\n"
        f"Scan Status Interval: {SCAN_STATUS_INTERVAL}s\n"
        f"Products: {', '.join(FUTURES_PRODUCTS)}"
    )

    next_account_update = time.time() + 15
    next_scan_status = time.time() + 15

    while True:
        loop_started = time.time()

        try:
            manage_trades(p)

            for product in FUTURES_PRODUCTS:
                if len(p["trades"]) >= MAX_OPEN_TRADES:
                    break

                if has_open_trade_for_product(p, product):
                    continue

                candles = get_candles(
                    product=product,
                    granularity=TREND_GRANULARITY,
                    limit=TREND_CANDLE_LIMIT
                )
                if not candles:
                    continue

                trend = trend_signal(candles)
                entry = entry_signal(candles, trend)

                if entry:
                    price = get_price(product)
                    if price:
                        open_trade(p, product, entry, price)

            now = time.time()

            if now >= next_account_update:
                send_telegram(build_account_update(p))
                next_account_update = now + ACCOUNT_UPDATE_INTERVAL

            if now >= next_scan_status:
                send_telegram(build_scan_status(p))
                next_scan_status = now + SCAN_STATUS_INTERVAL

            log(
                f"Scan complete | Value ${portfolio_value(p):.2f} | Cash ${p['cash']:.2f} | Trades {len(p['trades'])}"
            )

        except Exception as e:
            send_telegram(f"ERROR: {e}")

        elapsed = time.time() - loop_started
        sleep_for = max(1, SCAN_INTERVAL - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
