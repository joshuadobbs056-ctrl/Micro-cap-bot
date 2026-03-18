import os
import time
import math
import requests
import threading
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

# ============================================================
# COINBASE FUTURES SWING MODE PAPER TRADER
# ============================================================
# Swing mode design:
# - Uses HIGHER timeframe trend filter
# - Uses LOWER timeframe entry trigger
# - Opens PAPER long / short trades only on Coinbase futures products
# - Manages stop loss, optional take profit, and trailing stop
# - Holds through smaller counter-moves instead of flipping constantly
#
# IMPORTANT:
# - PAPER TRADING ONLY
# - Candle-based simulation, not tick-perfect fills
#
# ENV EXAMPLES:
#
# TELEGRAM_TOKEN=123456:ABCDEF
# CHAT_ID=123456789
#
# FUTURES_PRODUCTS=BTC-PERP-INTX,ETH-PERP-INTX,SOL-PERP-INTX
# SCAN_INTERVAL=60
# ACCOUNT_UPDATE_INTERVAL=60
#
# TREND_GRANULARITY=ONE_HOUR
# TREND_CANDLE_LIMIT=220
# ENTRY_GRANULARITY=FIVE_MINUTE
# ENTRY_CANDLE_LIMIT=120
#
# FAST_EMA=20
# SLOW_EMA=50
# ENTRY_EMA=9
#
# RISK_PER_TRADE_PCT=0.05
# START_BALANCE=2000
# MAX_OPEN_TRADES=3
#
# STOP_LOSS_PCT=0.02
# TAKE_PROFIT_PCT=0.06
# ENABLE_TAKE_PROFIT=on
#
# ENABLE_TRAILING_STOP=on
# TRAILING_STOP_PCT=0.015
# TRAILING_ACTIVATION_PCT=0.01
#
# ALLOW_LONGS=on
# ALLOW_SHORTS=on
#
# MIN_TREND_STRENGTH_PCT=0.002
# ENTRY_CONFIRM_BARS=2
# TELEGRAM_VERBOSE=on
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

FUTURES_PRODUCTS = [
    x.strip() for x in os.getenv("FUTURES_PRODUCTS", "BTC-PERP-INTX,ETH-PERP-INTX,SOL-PERP-INTX").split(",") if x.strip()
]

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
ACCOUNT_UPDATE_INTERVAL = int(os.getenv("ACCOUNT_UPDATE_INTERVAL", "60"))

TREND_GRANULARITY = os.getenv("TREND_GRANULARITY", "ONE_HOUR").strip().upper()
TREND_CANDLE_LIMIT = int(os.getenv("TREND_CANDLE_LIMIT", "220"))

ENTRY_GRANULARITY = os.getenv("ENTRY_GRANULARITY", "FIVE_MINUTE").strip().upper()
ENTRY_CANDLE_LIMIT = int(os.getenv("ENTRY_CANDLE_LIMIT", "120"))

FAST_EMA = int(os.getenv("FAST_EMA", "20"))
SLOW_EMA = int(os.getenv("SLOW_EMA", "50"))
ENTRY_EMA = int(os.getenv("ENTRY_EMA", "9"))

RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.05"))
START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))

STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.02"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.06"))
ENABLE_TAKE_PROFIT = os.getenv("ENABLE_TAKE_PROFIT", "on").strip().lower() == "on"

ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "on").strip().lower() == "on"
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.015"))
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "0.01"))

ALLOW_LONGS = os.getenv("ALLOW_LONGS", "on").strip().lower() == "on"
ALLOW_SHORTS = os.getenv("ALLOW_SHORTS", "on").strip().lower() == "on"

MIN_TREND_STRENGTH_PCT = float(os.getenv("MIN_TREND_STRENGTH_PCT", "0.002"))
ENTRY_CONFIRM_BARS = int(os.getenv("ENTRY_CONFIRM_BARS", "2"))
TELEGRAM_VERBOSE = os.getenv("TELEGRAM_VERBOSE", "on").strip().lower() == "on"

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product_id}/candles"

GRANULARITY_MAP = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
    "FIFTEEN_MINUTE": 900,
    "ONE_HOUR": 3600,
    "SIX_HOUR": 21600,
    "ONE_DAY": 86400,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_ts() -> str:
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message[:4000],
    }
    try:
        SESSION.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Telegram error: {e}")


def log(message: str, telegram: bool = False) -> None:
    stamp = utc_ts()
    line = f"[{stamp}] {message}"
    print(line)
    if telegram:
        send_telegram(line)


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    if period <= 1:
        return values[:]

    result = []
    k = 2 / (period + 1)
    ema_val = values[0]
    for price in values:
        ema_val = price * k + ema_val * (1 - k)
        result.append(ema_val)
    return result


def pct_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (b - a) / a


def fetch_candles(product_id: str, granularity_name: str, limit: int) -> List[Dict]:
    granularity = GRANULARITY_MAP.get(granularity_name)
    if not granularity:
        raise ValueError(f"Unsupported granularity: {granularity_name}")

    params = {"granularity": granularity}
    url = COINBASE_CANDLES_URL.format(product_id=product_id)

    try:
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        log(f"{product_id} fetch_candles error: {e}")
        return []

    candles = []
    for row in raw:
        # Coinbase candles format:
        # [time, low, high, open, close, volume]
        try:
            candles.append(
                {
                    "time": int(row[0]),
                    "low": float(row[1]),
                    "high": float(row[2]),
                    "open": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        except Exception:
            continue

    candles.sort(key=lambda x: x["time"])
    if limit > 0:
        candles = candles[-limit:]
    return candles


def get_latest_price(product_id: str, fallback_granularity: str = "ONE_MINUTE") -> Optional[float]:
    candles = fetch_candles(product_id, fallback_granularity, 3)
    if not candles:
        return None
    return candles[-1]["close"]


def calculate_trend_signal(candles: List[Dict]) -> Dict:
    if len(candles) < max(SLOW_EMA + 5, 30):
        return {"trend": "neutral", "strength": 0.0}

    closes = [c["close"] for c in candles]
    fast = ema(closes, FAST_EMA)
    slow = ema(closes, SLOW_EMA)

    last_close = closes[-1]
    last_fast = fast[-1]
    last_slow = slow[-1]
    slope_strength = pct_change(slow[-5], last_slow) if len(slow) >= 5 else 0.0

    if last_fast > last_slow and last_close > last_slow and abs(slope_strength) >= MIN_TREND_STRENGTH_PCT:
        return {"trend": "bullish", "strength": slope_strength}
    if last_fast < last_slow and last_close < last_slow and abs(slope_strength) >= MIN_TREND_STRENGTH_PCT:
        return {"trend": "bearish", "strength": slope_strength}
    return {"trend": "neutral", "strength": slope_strength}


def calculate_entry_signal(candles: List[Dict], higher_trend: str) -> Dict:
    if len(candles) < max(ENTRY_EMA + ENTRY_CONFIRM_BARS + 3, 20):
        return {"entry": None}

    closes = [c["close"] for c in candles]
    entry_line = ema(closes, ENTRY_EMA)

    last_close = closes[-1]
    prev_close = closes[-2]
    last_entry = entry_line[-1]
    prev_entry = entry_line[-2]

    confirms_above = True
    confirms_below = True
    bars = candles[-ENTRY_CONFIRM_BARS:]

    for c in bars:
        if c["close"] <= entry_line[closes.index(c["close"])]:
            confirms_above = False
        if c["close"] >= entry_line[closes.index(c["close"])]:
            confirms_below = False

    if higher_trend == "bullish":
        if prev_close <= prev_entry and last_close > last_entry:
            return {"entry": "long"}
        if confirms_above and last_close > last_entry:
            return {"entry": "long"}

    if higher_trend == "bearish":
        if prev_close >= prev_entry and last_close < last_entry:
            return {"entry": "short"}
        if confirms_below and last_close < last_entry:
            return {"entry": "short"}

    return {"entry": None}


def can_open_new_trade(portfolio: Dict) -> bool:
    return len(portfolio["trades"]) < portfolio["max_open_trades"]


def trade_exists(portfolio: Dict, product_id: str) -> bool:
    for t in portfolio["trades"]:
        if t["product_id"] == product_id:
            return True
    return False


def calculate_position_size(portfolio: Dict, entry_price: float) -> float:
    cash = portfolio["cash"]
    risk_dollars = cash * RISK_PER_TRADE_PCT
    if risk_dollars <= 0 or entry_price <= 0:
        return 0.0

    qty = risk_dollars / entry_price
    return max(qty, 0.0)


def open_trade(portfolio: Dict, product_id: str, side: str, price: float, trend_strength: float) -> Optional[Dict]:
    if not can_open_new_trade(portfolio):
        return None
    if trade_exists(portfolio, product_id):
        return None
    if price <= 0:
        return None

    qty = calculate_position_size(portfolio, price)
    entry_value = qty * price

    if qty <= 0 or entry_value <= 0:
        return None
    if entry_value > portfolio["cash"]:
        qty = portfolio["cash"] / price
        entry_value = qty * price

    if qty <= 0 or entry_value <= 0:
        return None

    portfolio["cash"] -= entry_value

    trade = {
        "product_id": product_id,
        "symbol": product_id,
        "side": side,
        "entry_price": price,
        "current_price": price,
        "qty": qty,
        "entry_value": entry_value,
        "current_value": entry_value,
        "opened_at": utc_ts(),
        "highest_price": price,
        "lowest_price": price,
        "trailing_active": False,
        "stop_price": price * (1 - STOP_LOSS_PCT) if side == "long" else price * (1 + STOP_LOSS_PCT),
        "take_profit_price": price * (1 + TAKE_PROFIT_PCT) if side == "long" else price * (1 - TAKE_PROFIT_PCT),
        "trail_price": None,
        "trend_strength": trend_strength,
        "peak_pnl": 0.0,
        "peak_pnl_pct": 0.0,
    }

    portfolio["trades"].append(trade)

    msg = (
        f"🟢 PAPER TRADE OPENED\n\n"
        f"{product_id}\n"
        f"Side: {side.upper()}\n"
        f"Entry: ${price:.2f}\n"
        f"Qty: {qty:.6f}\n"
        f"Entry Value: ${entry_value:.2f}\n"
        f"Stop: ${trade['stop_price']:.2f}\n"
        f"{'Take Profit: $' + format(trade['take_profit_price'], '.2f') if ENABLE_TAKE_PROFIT else 'Take Profit: OFF'}\n"
        f"Trend Strength: {trend_strength:.4%}"
    )
    send_telegram(msg)
    return trade


def close_trade(portfolio: Dict, trade: Dict, exit_price: float, reason: str) -> None:
    if exit_price <= 0:
        return

    qty = trade["qty"]
    final_value = qty * exit_price

    if trade["side"] == "long":
        pnl = final_value - trade["entry_value"]
    else:
        pnl = (trade["entry_price"] - exit_price) * qty

    if trade["side"] == "short":
        final_value = trade["entry_value"] + pnl

    portfolio["cash"] += final_value

    trade["exit_price"] = exit_price
    trade["closed_at"] = utc_ts()
    trade["close_reason"] = reason
    trade["final_value"] = final_value
    trade["pnl"] = pnl
    trade["pnl_pct"] = (pnl / trade["entry_value"] * 100.0) if trade["entry_value"] > 0 else 0.0

    portfolio["closed_trades"].append(trade)
    portfolio["trades"] = [t for t in portfolio["trades"] if t is not trade]

    emoji = "✅" if pnl >= 0 else "🔴"
    msg = (
        f"{emoji} PAPER TRADE CLOSED\n\n"
        f"{trade['product_id']}\n"
        f"Side: {trade['side'].upper()}\n"
        f"Entry: ${trade['entry_price']:.2f}\n"
        f"Exit: ${exit_price:.2f}\n"
        f"Entry Value: ${trade['entry_value']:.2f}\n"
        f"Final Value: ${final_value:.2f}\n"
        f"PnL: ${pnl:.2f}\n"
        f"PnL %: {trade['pnl_pct']:.2f}%\n"
        f"Peak PnL %: {trade.get('peak_pnl_pct', 0.0):.2f}%\n"
        f"Reason: {reason}"
    )
    send_telegram(msg)


def update_trade_marks(trade: Dict, current_price: float) -> None:
    trade["current_price"] = current_price

    if trade["side"] == "long":
        trade["current_value"] = trade["qty"] * current_price
        pnl = trade["current_value"] - trade["entry_value"]
    else:
        pnl = (trade["entry_price"] - current_price) * trade["qty"]
        trade["current_value"] = trade["entry_value"] + pnl

    pnl_pct = (pnl / trade["entry_value"] * 100.0) if trade["entry_value"] > 0 else 0.0

    if pnl > trade.get("peak_pnl", -999999999):
        trade["peak_pnl"] = pnl
    if pnl_pct > trade.get("peak_pnl_pct", -999999999):
        trade["peak_pnl_pct"] = pnl_pct

    if current_price > trade["highest_price"]:
        trade["highest_price"] = current_price
    if current_price < trade["lowest_price"]:
        trade["lowest_price"] = current_price


def manage_trade(portfolio: Dict, trade: Dict, current_price: float) -> None:
    update_trade_marks(trade, current_price)

    side = trade["side"]
    entry_price = trade["entry_price"]

    if ENABLE_TRAILING_STOP:
        if side == "long":
            activation_price = entry_price * (1 + TRAILING_ACTIVATION_PCT)
            if current_price >= activation_price:
                trade["trailing_active"] = True
                trail_candidate = current_price * (1 - TRAILING_STOP_PCT)
                if trade["trail_price"] is None or trail_candidate > trade["trail_price"]:
                    trade["trail_price"] = trail_candidate
        else:
            activation_price = entry_price * (1 - TRAILING_ACTIVATION_PCT)
            if current_price <= activation_price:
                trade["trailing_active"] = True
                trail_candidate = current_price * (1 + TRAILING_STOP_PCT)
                if trade["trail_price"] is None or trail_candidate < trade["trail_price"]:
                    trade["trail_price"] = trail_candidate

    if side == "long":
        if current_price <= trade["stop_price"]:
            close_trade(portfolio, trade, current_price, "stop_loss")
            return

        if ENABLE_TAKE_PROFIT and current_price >= trade["take_profit_price"]:
            close_trade(portfolio, trade, current_price, "take_profit")
            return

        if trade["trailing_active"] and trade["trail_price"] is not None and current_price <= trade["trail_price"]:
            close_trade(portfolio, trade, current_price, "trailing_stop")
            return

    else:
        if current_price >= trade["stop_price"]:
            close_trade(portfolio, trade, current_price, "stop_loss")
            return

        if ENABLE_TAKE_PROFIT and current_price <= trade["take_profit_price"]:
            close_trade(portfolio, trade, current_price, "take_profit")
            return

        if trade["trailing_active"] and trade["trail_price"] is not None and current_price >= trade["trail_price"]:
            close_trade(portfolio, trade, current_price, "trailing_stop")
            return


def send_account_update(portfolio: Dict) -> None:
    try:
        open_value = 0.0
        open_pnl = 0.0

        for trade in portfolio.get("trades", []):
            current_value = float(trade.get("current_value", 0.0))
            entry_value = float(trade.get("entry_value", 0.0))
            open_value += current_value
            open_pnl += (current_value - entry_value)

        total_value = float(portfolio.get("cash", 0.0)) + open_value
        start_balance = float(portfolio.get("start_balance", 0.0))
        total_pnl = total_value - start_balance
        total_pnl_pct = (total_pnl / start_balance * 100.0) if start_balance > 0 else 0.0

        lines = [
            "📊 ACCOUNT UPDATE",
            "",
            f"Starting Balance ${start_balance:.2f}",
            f"Current Value ${total_value:.2f}",
            "",
            f"Total Profit ${total_pnl:.2f}",
            f"PnL {total_pnl_pct:.2f}%",
            "",
            f"Cash ${float(portfolio.get('cash', 0.0)):.2f}",
            f"Open Trades {len(portfolio.get('trades', []))}/{int(portfolio.get('max_open_trades', 0))}",
        ]

        if portfolio.get("trades"):
            lines.append("")
            lines.append("Open Positions:")

            for trade in portfolio["trades"][:10]:
                symbol = trade.get("symbol") or trade.get("product_id") or "UNKNOWN"
                entry_value = float(trade.get("entry_value", 0.0))
                current_value = float(trade.get("current_value", 0.0))
                pnl = current_value - entry_value
                pnl_pct = (pnl / entry_value * 100.0) if entry_value > 0 else 0.0

                if trade.get("side") == "short":
                    pnl = float(trade.get("peak_pnl", 0.0)) if False else (trade["entry_price"] - trade["current_price"]) * trade["qty"]
                    pnl_pct = (pnl / entry_value * 100.0) if entry_value > 0 else 0.0

                lines.append(
                    f"• {symbol} {trade.get('side', '').upper()} | "
                    f"Value ${current_value:.2f} | "
                    f"PnL ${pnl:.2f} ({pnl_pct:.2f}%)"
                )

        send_telegram("\n".join(lines))

    except Exception as e:
        print(f"send_account_update error: {e}")


def account_update_loop(portfolio: Dict):
    while True:
        try:
            send_account_update(portfolio)
        except Exception as e:
            print(f"account_update_loop error: {e}")
        time.sleep(ACCOUNT_UPDATE_INTERVAL)


def build_portfolio() -> Dict:
    return {
        "start_balance": START_BALANCE,
        "cash": START_BALANCE,
        "trades": [],
        "closed_trades": [],
        "max_open_trades": MAX_OPEN_TRADES,
    }


def scan_product_for_entry(product_id: str) -> Optional[Tuple[str, float, float]]:
    trend_candles = fetch_candles(product_id, TREND_GRANULARITY, TREND_CANDLE_LIMIT)
    entry_candles = fetch_candles(product_id, ENTRY_GRANULARITY, ENTRY_CANDLE_LIMIT)

    if not trend_candles or not entry_candles:
        return None

    trend_info = calculate_trend_signal(trend_candles)
    trend = trend_info["trend"]
    strength = trend_info["strength"]

    if trend == "neutral":
        return None

    entry_info = calculate_entry_signal(entry_candles, trend)
    entry = entry_info.get("entry")
    if entry is None:
        return None

    price = entry_candles[-1]["close"]

    if entry == "long" and not ALLOW_LONGS:
        return None
    if entry == "short" and not ALLOW_SHORTS:
        return None

    return entry, price, strength


def portfolio_value(portfolio: Dict) -> float:
    total = portfolio["cash"]
    for t in portfolio["trades"]:
        total += float(t.get("current_value", 0.0))
    return total


def send_startup_message(portfolio: Dict) -> None:
    msg = (
        "🚀 Futures Swing Paper Trader Started\n\n"
        f"Products: {', '.join(FUTURES_PRODUCTS)}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n"
        f"Account Update Interval: {ACCOUNT_UPDATE_INTERVAL}s\n"
        f"Trend TF: {TREND_GRANULARITY}\n"
        f"Entry TF: {ENTRY_GRANULARITY}\n"
        f"Starting Balance: ${portfolio['start_balance']:.2f}\n"
        f"Max Open Trades: {portfolio['max_open_trades']}\n"
        f"Stop Loss: {STOP_LOSS_PCT * 100:.2f}%\n"
        f"Take Profit: {'ON ' + str(round(TAKE_PROFIT_PCT * 100, 2)) + '%' if ENABLE_TAKE_PROFIT else 'OFF'}\n"
        f"Trailing Stop: {'ON ' + str(round(TRAILING_STOP_PCT * 100, 2)) + '%' if ENABLE_TRAILING_STOP else 'OFF'}"
    )
    send_telegram(msg)


def main():
    portfolio = build_portfolio()
    send_startup_message(portfolio)

    threading.Thread(
        target=account_update_loop,
        args=(portfolio,),
        daemon=True
    ).start()

    while True:
        try:
            # Update existing trades first
            for trade in list(portfolio["trades"]):
                price = get_latest_price(trade["product_id"], ENTRY_GRANULARITY)
                if price is None:
                    continue
                manage_trade(portfolio, trade, price)

            # Scan for new trades
            for product_id in FUTURES_PRODUCTS:
                if not can_open_new_trade(portfolio):
                    break
                if trade_exists(portfolio, product_id):
                    continue

                signal = scan_product_for_entry(product_id)
                if not signal:
                    continue

                side, price, strength = signal
                open_trade(portfolio, product_id, side, price, strength)

            if TELEGRAM_VERBOSE:
                log(
                    f"Scan complete | Portfolio Value: ${portfolio_value(portfolio):.2f} | "
                    f"Cash: ${portfolio['cash']:.2f} | Open Trades: {len(portfolio['trades'])}"
                )

        except Exception as e:
            err = f"Main loop error: {e}"
            print(err)
            send_telegram(f"⚠️ {err}")

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
