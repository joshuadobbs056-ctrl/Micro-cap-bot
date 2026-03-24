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
    p.strip()
    for p in os.getenv(
        "FUTURES_PRODUCTS",
        "BTC-PERP-INTX,ETH-PERP-INTX,SOL-PERP-INTX"
    ).split(",")
    if p.strip()
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

ENTRY_GRANULARITY = os.getenv("ENTRY_GRANULARITY", "FIVE_MINUTE").strip()
ENTRY_CANDLE_LIMIT = int(os.getenv("ENTRY_CANDLE_LIMIT", "120"))

DEBUG_SIGNALS = os.getenv("DEBUG_SIGNALS", "on").strip().lower() == "on"

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
        r = SESSION.get(PRODUCT_URL.format(product_id=product), timeout=10)
        r.raise_for_status()
        data = r.json()
        price = data.get("price")
        if price is None:
            return None
        return float(price)
    except Exception as e:
        print(f"get_price error for {product}: {e}")
        return None


def get_candles(product: str, granularity: str, limit: int) -> List[Dict]:
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
            timeout=10
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
    if not data or period <= 0:
        return []

    k = 2 / (period + 1)
    val = data[0]
    out = []

    for price in data:
        val = price * k + val * (1 - k)
        out.append(val)

    return out


def trend_signal(candles: List[Dict]) -> Optional[str]:
    closed = candles[:-1]

    if len(closed) < max(FAST_EMA, SLOW_EMA) + 3:
        return None

    closes = [float(x["close"]) for x in closed]
    fast = ema(closes, FAST_EMA)
    slow = ema(closes, SLOW_EMA)

    if len(fast) < 3 or len(slow) < 3:
        return None

    if fast[-1] > slow[-1] and fast[-1] > fast[-2]:
        return "long"

    if fast[-1] < slow[-1] and fast[-1] < fast[-2]:
        return "short"

    return None


def entry_signal(candles: List[Dict], trend: Optional[str]) -> Optional[str]:
    if trend is None:
        return None

    closed = candles[:-1]

    if len(closed) < ENTRY_EMA + 4:
        return None

    closes = [float(x["close"]) for x in closed]
    line = ema(closes, ENTRY_EMA)

    if len(closes) < 3 or len(line) < 3:
        return None

    # exact cross on the most recent CLOSED candle, but on a lower timeframe
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
        "base_cash": START_BALANCE,
        "reserved_profit": 0.0,
        "start": START_BALANCE,
        "trades": [],
        "closed": [],
    }


def tradable_cash(p: Dict) -> float:
    return p["base_cash"]


def total_cash(p: Dict) -> float:
    return p["base_cash"] + p["reserved_profit"]


def portfolio_value(p: Dict) -> float:
    total = total_cash(p)
    for t in p["trades"]:
        total += t["current_value"]
    return total


def has_open_trade_for_product(p: Dict, product: str) -> bool:
    return any(t["product"] == product for t in p["trades"])


def sweep_locked_profit(p: Dict) -> None:
    excess = max(0.0, p["base_cash"] - p["start"])
    if excess > 0:
        p["base_cash"] -= excess
        p["reserved_profit"] += excess

# ================= TRADING =================

def open_trade(p: Dict, product: str, side: str, price: float) -> None:
    if has_open_trade_for_product(p, product):
        return

    value = min(PURCHASE_AMOUNT_USD, tradable_cash(p))
    if value <= 0:
        return

    qty = value / price
    p["base_cash"] -= value

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
        "breakeven": False,
        "opened_at": utc(),
    }

    p["trades"].append(trade)

    send_telegram(
        f"🟢 OPEN {side.upper()} {product}\n"
        f"Entry: ${price:.2f}\n"
        f"Size: ${value:.2f}\n"
        f"Qty: {qty:.8f}\n"
        f"Tradable Cash Left: ${p['base_cash']:.2f}\n"
        f"Locked Profit: ${p['reserved_profit']:.2f}\n"
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
    t["current_value"] = value

    p["base_cash"] += value
    sweep_locked_profit(p)

    if t in p["trades"]:
        p["trades"].remove(t)
    p["closed"].append(t)

    pnl_pct = (pnl / t["entry_value"]) * 100 if t["entry_value"] else 0.0

    send_telegram(
        f"🔴 CLOSE {t['product']} {t['side'].upper()}\n"
        f"Entry: ${t['entry']:.2f}\n"
        f"Exit: ${price:.2f}\n"
        f"PnL: ${pnl:.2f} ({pnl_pct:.2f}%)\n"
        f"Reason: {reason}\n"
        f"Tradable Cash: ${p['base_cash']:.2f}\n"
        f"Locked Profit: ${p['reserved_profit']:.2f}\n"
        f"Closed: {t['closed_at']} UTC"
    )


def manage_trades(p: Dict) -> None:
    for t in list(p["trades"]):
        price = get_price(t["product"])
        if price is None:
            continue

        t["price"] = price

        if t["side"] == "long":
            t["current_value"] = t["qty"] * price
            pnl = t["current_value"] - t["entry_value"]
            pnl_pct = (price - t["entry"]) / t["entry"]

            if pnl > t["peak"]:
                t["peak"] = pnl

            if pnl_pct >= BREAKEVEN_TRIGGER and not t["breakeven"]:
                t["trail"] = t["entry"]
                t["breakeven"] = True

            if pnl_pct >= TRAILING_ACTIVATE:
                t["active"] = True
                new_trail = price * (1 - TRAILING_STOP_PCT)
                if t["trail"] is None or new_trail > t["trail"]:
                    t["trail"] = new_trail

            if price <= t["entry"] * (1 - STOP_LOSS_PCT):
                close_trade(p, t, price, "SL")
                continue

            if price >= t["entry"] * (1 + TAKE_PROFIT_PCT):
                close_trade(p, t, price, "TP")
                continue

            if t["active"] and t["trail"] is not None and price <= t["trail"]:
                close_trade(p, t, price, "TRAIL")
                continue

        else:
            pnl = (t["entry"] - price) * t["qty"]
            t["current_value"] = t["entry_value"] + pnl
            pnl_pct = (t["entry"] - price) / t["entry"]

            if pnl > t["peak"]:
                t["peak"] = pnl

            if pnl_pct >= BREAKEVEN_TRIGGER and not t["breakeven"]:
                t["trail"] = t["entry"]
                t["breakeven"] = True

            if pnl_pct >= TRAILING_ACTIVATE:
                t["active"] = True
                new_trail = price * (1 + TRAILING_STOP_PCT)
                if t["trail"] is None or new_trail < t["trail"]:
                    t["trail"] = new_trail

            if price >= t["entry"] * (1 + STOP_LOSS_PCT):
                close_trade(p, t, price, "SL")
                continue

            if price <= t["entry"] * (1 - TAKE_PROFIT_PCT):
                close_trade(p, t, price, "TP")
                continue

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
        f"Tradable Cash: ${p['base_cash']:.2f}",
        f"Locked Profit: ${p['reserved_profit']:.2f}",
        f"PnL: ${pnl:.2f} ({pct:.2f}%)",
        f"Open Trades: {len(p['trades'])}",
        f"Closed Trades: {len(p['closed'])}",
    ]

    if p["trades"]:
        lines.append("")
        lines.append("Open Positions:")
        for t in p["trades"]:
            if t["side"] == "long":
                trade_pnl = t["current_value"] - t["entry_value"]
            else:
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
                f"Breakeven Armed: {'Yes' if t['breakeven'] else 'No'}",
            ])

            if t["trail"] is not None:
                lines.append(f"Trail: ${t['trail']:.2f}")

    return "\n".join(lines)


def build_scan_status(p: Dict) -> str:
    total = portfolio_value(p)
    return (
        "🛰️ SCAN STATUS\n\n"
        f"Value: ${total:.2f}\n"
        f"Tradable Cash: ${p['base_cash']:.2f}\n"
        f"Locked Profit: ${p['reserved_profit']:.2f}\n"
        f"Open Trades: {len(p['trades'])}\n"
        f"Watching: {', '.join(FUTURES_PRODUCTS)}\n"
        f"Trend TF: {TREND_GRANULARITY}\n"
        f"Entry TF: {ENTRY_GRANULARITY}\n"
        f"Next scan in: {SCAN_INTERVAL}s"
    )


def build_signal_debug(product: str, trend: Optional[str], entry: Optional[str]) -> str:
    return (
        f"🔎 {product}\n"
        f"Trend: {trend or 'none'}\n"
        f"Entry: {entry or 'none'}\n"
        f"Trend TF: {TREND_GRANULARITY}\n"
        f"Entry TF: {ENTRY_GRANULARITY}"
    )

# ================= MAIN =================

def main() -> None:
    p = portfolio()

    send_telegram(
        "🚀 BOT STARTED (MTF ENTRY ENABLED)\n"
        f"Start Balance: ${START_BALANCE:.2f}\n"
        f"Position Size: ${PURCHASE_AMOUNT_USD:.2f}\n"
        f"Max Open Trades: {MAX_OPEN_TRADES}\n"
        f"Stop Loss: {STOP_LOSS_PCT * 100:.2f}%\n"
        f"Take Profit: {TAKE_PROFIT_PCT * 100:.2f}%\n"
        f"Trail Activate: {TRAILING_ACTIVATE * 100:.2f}%\n"
        f"Trailing Stop: {TRAILING_STOP_PCT * 100:.2f}%\n"
        f"Breakeven Trigger: {BREAKEVEN_TRIGGER * 100:.2f}%\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n"
        f"Trend TF: {TREND_GRANULARITY}\n"
        f"Entry TF: {ENTRY_GRANULARITY}\n"
        f"Products: {', '.join(FUTURES_PRODUCTS)}"
    )

    next_account_update = time.time() + 15
    next_scan_status = time.time() + 15
    next_debug = time.time() + 30

    while True:
        loop_started = time.time()

        try:
            manage_trades(p)

            debug_lines = []

            for product in FUTURES_PRODUCTS:
                if len(p["trades"]) >= MAX_OPEN_TRADES:
                    break

                if has_open_trade_for_product(p, product):
                    continue

                trend_candles = get_candles(product, TREND_GRANULARITY, TREND_CANDLE_LIMIT)
                if not trend_candles:
                    debug_lines.append(f"{product}: no trend candles")
                    continue

                entry_candles = get_candles(product, ENTRY_GRANULARITY, ENTRY_CANDLE_LIMIT)
                if not entry_candles:
                    debug_lines.append(f"{product}: no entry candles")
                    continue

                trend = trend_signal(trend_candles)
                entry = entry_signal(entry_candles, trend)

                debug_lines.append(f"{product}: trend={trend or 'none'} entry={entry or 'none'}")

                if entry:
                    price = get_price(product)
                    if price is not None:
                        open_trade(p, product, entry, price)

            now = time.time()

            if now >= next_account_update:
                send_telegram(build_account_update(p))
                next_account_update = now + ACCOUNT_UPDATE_INTERVAL

            if now >= next_scan_status:
                send_telegram(build_scan_status(p))
                next_scan_status = now + SCAN_STATUS_INTERVAL

            if DEBUG_SIGNALS and now >= next_debug:
                send_telegram(" | ".join(debug_lines[:8]))
                next_debug = now + 300

            log(
                f"Scan complete | Value ${portfolio_value(p):.2f} | "
                f"Tradable ${p['base_cash']:.2f} | Locked ${p['reserved_profit']:.2f} | "
                f"Trades {len(p['trades'])} | "
                + " ; ".join(debug_lines)
            )

        except Exception as e:
            send_telegram(f"ERROR: {e}")
            print("Main loop error:", e)

        elapsed = time.time() - loop_started
        sleep_for = max(1, SCAN_INTERVAL - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
