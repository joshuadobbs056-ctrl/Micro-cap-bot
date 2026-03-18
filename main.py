import os
import time
import math
import requests
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

# ============================================================
# COINBASE FUTURES STRONG-MOVE PAPER TRADER
# ============================================================
# What it does:
# - Pulls recent candles from Coinbase public market-data endpoints
# - Scores for strong UP / DOWN movement setups
# - Opens PAPER long / short trades only on Coinbase futures products
# - Manages stop loss, take profit, and trailing stop
# - Sends Telegram alerts for entries, updates, exits, and summaries
#
# IMPORTANT:
# - PAPER TRADING ONLY
# - Candle-based simulation, not tick-perfect fills
# - Trailing/SL/TP uses candle high/low logic when possible
#
# ENV EXAMPLES:
#
# TELEGRAM_TOKEN=123456:ABCDEF
# CHAT_ID=123456789
#
# SCAN_INTERVAL=60
# FUTURES_PRODUCTS=BTC-PERP-INTX,ETH-PERP-INTX,SOL-PERP-INTX
# GRANULARITY=ONE_MINUTE
# CANDLE_LIMIT=150
#
# RSI_PERIOD=14
# MOMENTUM_BARS=5
# VOLUME_SPIKE_MULT=2.0
# RANGE_SPIKE_MULT=1.8
# ALERT_SCORE_THRESHOLD=3
# ALERT_COOLDOWN_MINUTES=20
#
# PAPER_TRADING=on
# START_BALANCE=2000
# MAX_OPEN_TRADES=3
# POSITION_SIZE_MODE=fixed          # fixed or percent
# FIXED_SIZE_USD=100
# POSITION_SIZE_PCT=0.10            # 10% of current balance if mode=percent
# LEVERAGE=1
#
# STOP_LOSS_PCT=2.0
# TAKE_PROFIT_PCT=4.0
# TRAILING_STOP_ENABLED=on
# TRAILING_STOP_PCT=1.25
# ENABLE_POSITION_UPDATES=on
# POSITION_UPDATE_COOLDOWN_MINUTES=10
# SUMMARY_EVERY_N_SCANS=15
#
# MIN_NOTIONAL_USD=10
# ALLOW_LONGS=on
# ALLOW_SHORTS=on
# DEBUG=on
# ============================================================

BASE_URL = "https://api.coinbase.com/api/v3/brokerage/market/products"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
FUTURES_PRODUCTS = [
    x.strip() for x in os.getenv("FUTURES_PRODUCTS", "BTC-PERP-INTX,ETH-PERP-INTX").split(",")
    if x.strip()
]

GRANULARITY = os.getenv("GRANULARITY", "ONE_MINUTE").strip().upper()
CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "150"))

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
MOMENTUM_BARS = int(os.getenv("MOMENTUM_BARS", "5"))
VOLUME_SPIKE_MULT = float(os.getenv("VOLUME_SPIKE_MULT", "2.0"))
RANGE_SPIKE_MULT = float(os.getenv("RANGE_SPIKE_MULT", "1.8"))
ALERT_SCORE_THRESHOLD = int(os.getenv("ALERT_SCORE_THRESHOLD", "3"))
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "20"))

PAPER_TRADING = os.getenv("PAPER_TRADING", "on").strip().lower() == "on"
START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))
POSITION_SIZE_MODE = os.getenv("POSITION_SIZE_MODE", "fixed").strip().lower()
FIXED_SIZE_USD = float(os.getenv("FIXED_SIZE_USD", "100"))
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "0.10"))
LEVERAGE = float(os.getenv("LEVERAGE", "1"))

STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "on").strip().lower() == "on"
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "1.25"))

ENABLE_POSITION_UPDATES = os.getenv("ENABLE_POSITION_UPDATES", "on").strip().lower() == "on"
POSITION_UPDATE_COOLDOWN_MINUTES = int(os.getenv("POSITION_UPDATE_COOLDOWN_MINUTES", "10"))
SUMMARY_EVERY_N_SCANS = int(os.getenv("SUMMARY_EVERY_N_SCANS", "15"))

MIN_NOTIONAL_USD = float(os.getenv("MIN_NOTIONAL_USD", "10"))
ALLOW_LONGS = os.getenv("ALLOW_LONGS", "on").strip().lower() == "on"
ALLOW_SHORTS = os.getenv("ALLOW_SHORTS", "on").strip().lower() == "on"

DEBUG = os.getenv("DEBUG", "off").strip().lower() == "on"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Coinbase-Futures-PaperTrader/2.0",
    "Accept": "application/json",
    "Cache-Control": "no-cache",
})

# ------------------------------------------------------------
# GLOBAL PAPER STATE
# ------------------------------------------------------------
PAPER_STATE = {
    "starting_balance": START_BALANCE,
    "cash": START_BALANCE,
    "realized_pnl": 0.0,
    "closed_trades": 0,
    "wins": 0,
    "losses": 0,
}

# product_id -> position
OPEN_POSITIONS: Dict[str, Dict] = {}

LAST_ALERT_AT: Dict[str, int] = {}
LAST_POSITION_UPDATE_AT: Dict[str, int] = {}

SCAN_COUNT = 0


# ------------------------------------------------------------
# UTILS
# ------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def utc_now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def fmt_num(x: float, decimals: int = 4) -> str:
    if x == 0:
        return "0"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:.{decimals}f}"
    return f"{x:.8f}"


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def send_telegram(msg: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("Telegram not configured. Printing message instead:")
        print(msg)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = SESSION.post(
            url,
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=15
        )
        if resp.status_code != 200:
            log(f"Telegram send failed: {resp.status_code} {resp.text[:300]}")
    except Exception as e:
        log(f"Telegram error: {e}")


def is_futures_product(product_id: str) -> bool:
    pid = (product_id or "").upper()
    return ("PERP" in pid) or ("FUTURE" in pid) or ("INTX" in pid)


# ------------------------------------------------------------
# COINBASE DATA
# ------------------------------------------------------------
def get_candles(product_id: str,
                granularity: str = GRANULARITY,
                limit: int = CANDLE_LIMIT) -> Optional[List[Dict]]:
    end_ts = utc_now_ts()
    granularity_seconds = {
        "ONE_MINUTE": 60,
        "FIVE_MINUTE": 300,
        "FIFTEEN_MINUTE": 900,
        "THIRTY_MINUTE": 1800,
        "ONE_HOUR": 3600,
        "TWO_HOUR": 7200,
        "FOUR_HOUR": 14400,
        "SIX_HOUR": 21600,
        "ONE_DAY": 86400,
    }.get(granularity, 60)

    start_ts = end_ts - (limit * granularity_seconds)

    url = f"{BASE_URL}/{product_id}/candles"
    params = {
        "start": str(start_ts),
        "end": str(end_ts),
        "granularity": granularity,
        "limit": str(limit),
    }

    try:
        resp = SESSION.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            log(f"Coinbase candles failed for {product_id}: {resp.status_code} {resp.text[:300]}")
            return None

        data = resp.json()
        candles = data.get("candles", [])
        if not candles:
            if DEBUG:
                log(f"No candles returned for {product_id}")
            return None

        candles.sort(key=lambda x: int(x["start"]))
        return candles

    except Exception as e:
        log(f"get_candles error for {product_id}: {e}")
        return None


# ------------------------------------------------------------
# INDICATORS
# ------------------------------------------------------------
def extract_ohlcv(candles: List[Dict]) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for c in candles:
        opens.append(safe_float(c.get("open")))
        highs.append(safe_float(c.get("high")))
        lows.append(safe_float(c.get("low")))
        closes.append(safe_float(c.get("close")))
        volumes.append(safe_float(c.get("volume", 0) or 0))
    return opens, highs, lows, closes, volumes


def calc_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def sma(values: List[float], length: int) -> float:
    if not values:
        return 0.0
    if len(values) < length:
        return sum(values) / len(values)
    return sum(values[-length:]) / length


def avg_range(highs: List[float], lows: List[float], lookback: int = 20) -> float:
    ranges = [max(h - l, 0.0) for h, l in zip(highs, lows)]
    return sma(ranges, lookback)


def recent_momentum_pct(closes: List[float], bars: int = 5) -> float:
    if len(closes) <= bars:
        return 0.0
    return pct_change(closes[-1], closes[-1 - bars])


def candle_body_pct(open_price: float, close_price: float) -> float:
    if open_price == 0:
        return 0.0
    return abs((close_price - open_price) / open_price) * 100.0


# ------------------------------------------------------------
# SIGNAL LOGIC
# ------------------------------------------------------------
def analyze_product(product_id: str) -> Optional[Dict]:
    candles = get_candles(product_id)
    if not candles or len(candles) < max(RSI_PERIOD + 5, 30):
        return None

    opens, highs, lows, closes, volumes = extract_ohlcv(candles)

    last_open = opens[-1]
    last_high = highs[-1]
    last_low = lows[-1]
    last_close = closes[-1]
    last_volume = volumes[-1]

    rsi = calc_rsi(closes, RSI_PERIOD)
    momentum_pct = recent_momentum_pct(closes, MOMENTUM_BARS)

    avg_vol = sma(volumes[:-1], 20) if len(volumes) > 20 else sma(volumes, max(1, len(volumes)))
    vol_multiple = (last_volume / avg_vol) if avg_vol > 0 else 0.0

    this_range = max(last_high - last_low, 0.0)
    normal_range = avg_range(highs[:-1], lows[:-1], 20) if len(highs) > 20 else avg_range(highs, lows, max(3, len(highs)))
    range_multiple = (this_range / normal_range) if normal_range > 0 else 0.0

    body_pct = candle_body_pct(last_open, last_close)

    last20_high = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
    last20_low = min(lows[-21:-1]) if len(lows) >= 21 else min(lows[:-1])

    breakout_up = last_close > last20_high
    breakout_down = last_close < last20_low

    bullish_score = 0
    bearish_score = 0
    reasons_up = []
    reasons_down = []

    if vol_multiple >= VOLUME_SPIKE_MULT:
        bullish_score += 1
        bearish_score += 1
        reasons_up.append(f"volume x{vol_multiple:.2f}")
        reasons_down.append(f"volume x{vol_multiple:.2f}")

    if range_multiple >= RANGE_SPIKE_MULT:
        bullish_score += 1
        bearish_score += 1
        reasons_up.append(f"range x{range_multiple:.2f}")
        reasons_down.append(f"range x{range_multiple:.2f}")

    if momentum_pct >= 0.35:
        bullish_score += 1
        reasons_up.append(f"momentum {momentum_pct:+.2f}%")
    elif momentum_pct <= -0.35:
        bearish_score += 1
        reasons_down.append(f"momentum {momentum_pct:+.2f}%")

    if rsi >= 63:
        bullish_score += 1
        reasons_up.append(f"RSI {rsi:.1f}")
    elif rsi <= 37:
        bearish_score += 1
        reasons_down.append(f"RSI {rsi:.1f}")

    if breakout_up:
        bullish_score += 1
        reasons_up.append("20-candle breakout")
    if breakout_down:
        bearish_score += 1
        reasons_down.append("20-candle breakdown")

    if body_pct >= 0.20:
        if last_close > last_open:
            bullish_score += 1
            reasons_up.append(f"strong body {body_pct:.2f}%")
        elif last_close < last_open:
            bearish_score += 1
            reasons_down.append(f"strong body {body_pct:.2f}%")

    direction = None
    side = None
    score = 0
    reasons = []

    if bullish_score >= ALERT_SCORE_THRESHOLD and bullish_score > bearish_score and ALLOW_LONGS:
        direction = "UP"
        side = "LONG"
        score = bullish_score
        reasons = reasons_up
    elif bearish_score >= ALERT_SCORE_THRESHOLD and bearish_score > bullish_score and ALLOW_SHORTS:
        direction = "DOWN"
        side = "SHORT"
        score = bearish_score
        reasons = reasons_down

    if not direction:
        if DEBUG:
            log(
                f"{product_id} no signal | "
                f"bull={bullish_score} bear={bearish_score} "
                f"rsi={rsi:.1f} mom={momentum_pct:+.2f}% "
                f"volx={vol_multiple:.2f} rangex={range_multiple:.2f}"
            )
        return None

    return {
        "product_id": product_id,
        "direction": direction,
        "side": side,
        "score": score,
        "price": last_close,
        "last_high": last_high,
        "last_low": last_low,
        "last_open": last_open,
        "rsi": rsi,
        "momentum_pct": momentum_pct,
        "vol_multiple": vol_multiple,
        "range_multiple": range_multiple,
        "reasons": reasons[:5],
        "timestamp": utc_now_ts(),
    }


# ------------------------------------------------------------
# PAPER TRADING
# ------------------------------------------------------------
def available_cash() -> float:
    return PAPER_STATE["cash"]


def calc_position_notional(entry_price: float) -> float:
    if POSITION_SIZE_MODE == "percent":
        raw = available_cash() * POSITION_SIZE_PCT
    else:
        raw = FIXED_SIZE_USD

    raw = min(raw, available_cash())
    raw = max(0.0, raw)

    if raw < MIN_NOTIONAL_USD:
        return 0.0

    return raw


def calc_contract_qty(notional_usd: float, entry_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    # notional * leverage / price controls the leveraged size in units
    qty = (notional_usd * LEVERAGE) / entry_price
    return max(0.0, qty)


def mark_position_pnl(position: Dict, current_price: float) -> Tuple[float, float]:
    entry = position["entry_price"]
    qty = position["qty"]
    side = position["side"]

    if side == "LONG":
        pnl = (current_price - entry) * qty
    else:
        pnl = (entry - current_price) * qty

    pnl_pct_on_margin = 0.0
    if position["margin_used"] > 0:
        pnl_pct_on_margin = (pnl / position["margin_used"]) * 100.0

    return pnl, pnl_pct_on_margin


def compute_exit_prices(entry_price: float, side: str) -> Tuple[float, float]:
    if side == "LONG":
        stop_loss = entry_price * (1.0 - STOP_LOSS_PCT / 100.0)
        take_profit = entry_price * (1.0 + TAKE_PROFIT_PCT / 100.0)
    else:
        stop_loss = entry_price * (1.0 + STOP_LOSS_PCT / 100.0)
        take_profit = entry_price * (1.0 - TAKE_PROFIT_PCT / 100.0)
    return stop_loss, take_profit


def open_paper_position(signal: Dict) -> Optional[Dict]:
    product_id = signal["product_id"]
    if product_id in OPEN_POSITIONS:
        return None

    if len(OPEN_POSITIONS) >= MAX_OPEN_TRADES:
        if DEBUG:
            log(f"Max open trades reached. Skipping {product_id}")
        return None

    entry_price = signal["price"]
    margin_used = calc_position_notional(entry_price)
    if margin_used <= 0:
        if DEBUG:
            log(f"Not enough available paper cash to open {product_id}")
        return None

    qty = calc_contract_qty(margin_used, entry_price)
    if qty <= 0:
        if DEBUG:
            log(f"Qty computed to zero for {product_id}")
        return None

    stop_loss, take_profit = compute_exit_prices(entry_price, signal["side"])

    position = {
        "product_id": product_id,
        "side": signal["side"],
        "direction": signal["direction"],
        "score": signal["score"],
        "entry_price": entry_price,
        "entry_ts": utc_now_ts(),
        "qty": qty,
        "margin_used": margin_used,
        "notional": qty * entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "trailing_stop": None,
        "highest_price": entry_price,
        "lowest_price": entry_price,
        "peak_pnl": 0.0,
        "peak_pnl_pct": 0.0,
        "last_update_ts": 0,
        "entry_reasons": signal["reasons"][:],
    }

    if TRAILING_STOP_ENABLED:
        if signal["side"] == "LONG":
            position["trailing_stop"] = entry_price * (1.0 - TRAILING_STOP_PCT / 100.0)
        else:
            position["trailing_stop"] = entry_price * (1.0 + TRAILING_STOP_PCT / 100.0)

    OPEN_POSITIONS[product_id] = position
    PAPER_STATE["cash"] -= margin_used

    return position


def close_paper_position(product_id: str, exit_price: float, reason: str) -> Optional[Dict]:
    position = OPEN_POSITIONS.get(product_id)
    if not position:
        return None

    pnl, pnl_pct = mark_position_pnl(position, exit_price)

    PAPER_STATE["cash"] += position["margin_used"] + pnl
    PAPER_STATE["realized_pnl"] += pnl
    PAPER_STATE["closed_trades"] += 1
    if pnl >= 0:
        PAPER_STATE["wins"] += 1
    else:
        PAPER_STATE["losses"] += 1

    closed = {
        **position,
        "exit_price": exit_price,
        "exit_ts": utc_now_ts(),
        "exit_reason": reason,
        "realized_pnl": pnl,
        "realized_pnl_pct": pnl_pct,
    }

    del OPEN_POSITIONS[product_id]
    return closed


def should_send_position_update(product_id: str) -> bool:
    key = f"posupdate:{product_id}"
    now_ts = utc_now_ts()
    last_ts = LAST_POSITION_UPDATE_AT.get(key, 0)
    cooldown_seconds = POSITION_UPDATE_COOLDOWN_MINUTES * 60

    if now_ts - last_ts >= cooldown_seconds:
        LAST_POSITION_UPDATE_AT[key] = now_ts
        return True
    return False


def update_trailing_stop(position: Dict, current_high: float, current_low: float) -> None:
    if not TRAILING_STOP_ENABLED:
        return

    side = position["side"]

    if side == "LONG":
        if current_high > position["highest_price"]:
            position["highest_price"] = current_high
        new_trailing = position["highest_price"] * (1.0 - TRAILING_STOP_PCT / 100.0)
        if position["trailing_stop"] is None or new_trailing > position["trailing_stop"]:
            position["trailing_stop"] = new_trailing
    else:
        if current_low < position["lowest_price"]:
            position["lowest_price"] = current_low
        new_trailing = position["lowest_price"] * (1.0 + TRAILING_STOP_PCT / 100.0)
        if position["trailing_stop"] is None or new_trailing < position["trailing_stop"]:
            position["trailing_stop"] = new_trailing


def evaluate_position_exit(position: Dict, current_open: float, current_high: float, current_low: float, current_close: float) -> Optional[Tuple[float, str]]:
    side = position["side"]
    stop_loss = position["stop_loss"]
    take_profit = position["take_profit"]
    trailing_stop = position["trailing_stop"]

    # Conservative priority:
    # 1) Stop loss
    # 2) Trailing stop
    # 3) Take profit
    #
    # This avoids overstating wins when a candle spans multiple levels.

    if side == "LONG":
        if current_low <= stop_loss:
            return stop_loss, "stop loss hit"
        if trailing_stop is not None and current_low <= trailing_stop:
            return trailing_stop, "trailing stop hit"
        if current_high >= take_profit:
            return take_profit, "take profit hit"
    else:
        if current_high >= stop_loss:
            return stop_loss, "stop loss hit"
        if trailing_stop is not None and current_high >= trailing_stop:
            return trailing_stop, "trailing stop hit"
        if current_low <= take_profit:
            return take_profit, "take profit hit"

    return None


def manage_open_position(signal: Dict) -> Optional[Dict]:
    product_id = signal["product_id"]
    position = OPEN_POSITIONS.get(product_id)
    if not position:
        return None

    current_price = signal["price"]
    current_high = signal["last_high"]
    current_low = signal["last_low"]
    current_open = signal["last_open"]

    update_trailing_stop(position, current_high, current_low)

    pnl, pnl_pct = mark_position_pnl(position, current_price)
    if pnl > position["peak_pnl"]:
        position["peak_pnl"] = pnl
        position["peak_pnl_pct"] = pnl_pct

    exit_decision = evaluate_position_exit(position, current_open, current_high, current_low, current_price)
    if exit_decision:
        exit_price, reason = exit_decision
        return close_paper_position(product_id, exit_price, reason)

    return None


def total_open_pnl(mark_prices: Dict[str, float]) -> float:
    total = 0.0
    for product_id, position in OPEN_POSITIONS.items():
        current_price = mark_prices.get(product_id, position["entry_price"])
        pnl, _ = mark_position_pnl(position, current_price)
        total += pnl
    return total


def current_equity(mark_prices: Dict[str, float]) -> float:
    return PAPER_STATE["cash"] + sum(p["margin_used"] for p in OPEN_POSITIONS.values()) + total_open_pnl(mark_prices)


# ------------------------------------------------------------
# ALERT FORMATTING
# ------------------------------------------------------------
def build_signal_alert(signal: Dict) -> str:
    product_id = signal["product_id"]
    direction = signal["direction"]
    score = signal["score"]
    price = signal["price"]
    rsi = signal["rsi"]
    momentum_pct = signal["momentum_pct"]
    vol_multiple = signal["vol_multiple"]
    range_multiple = signal["range_multiple"]
    reasons = signal["reasons"]

    if direction == "UP":
        header = "🟢⬆️ STRONG MOVE UP"
        icon = "BULLISH"
    else:
        header = "🔴⬇️ STRONG MOVE DOWN"
        icon = "BEARISH"

    reason_block = "\n".join([f"• {r}" for r in reasons])

    return (
        f"{header}\n\n"
        f"{product_id}\n"
        f"Signal: {icon}\n"
        f"Score: {score}\n\n"
        f"Price: {fmt_num(price, 6)}\n"
        f"RSI: {rsi:.1f}\n"
        f"Momentum: {momentum_pct:+.2f}%\n"
        f"Volume Spike: x{vol_multiple:.2f}\n"
        f"Range Expansion: x{range_multiple:.2f}\n\n"
        f"Reasons:\n{reason_block}"
    )


def build_entry_alert(position: Dict, signal: Dict) -> str:
    side = position["side"]
    arrow = "🟢⬆️" if side == "LONG" else "🔴⬇️"

    reasons = "\n".join([f"• {r}" for r in position["entry_reasons"]])

    return (
        f"{arrow} PAPER TRADE OPEN\n\n"
        f"{position['product_id']}\n"
        f"Side: {side}\n"
        f"Score: {position['score']}\n\n"
        f"Entry: {fmt_num(position['entry_price'], 6)}\n"
        f"Qty: {fmt_num(position['qty'], 6)}\n"
        f"Notional: ${fmt_num(position['notional'], 2)}\n"
        f"Margin Used: ${fmt_num(position['margin_used'], 2)}\n"
        f"Leverage: {LEVERAGE}x\n\n"
        f"Stop Loss: {fmt_num(position['stop_loss'], 6)}\n"
        f"Take Profit: {fmt_num(position['take_profit'], 6)}\n"
        f"Trailing Stop: {fmt_num(position['trailing_stop'], 6) if position['trailing_stop'] is not None else 'off'}\n\n"
        f"Reasons:\n{reasons}"
    )


def build_position_update(position: Dict, current_price: float) -> str:
    pnl, pnl_pct = mark_position_pnl(position, current_price)

    return (
        f"📊 PAPER POSITION UPDATE\n\n"
        f"{position['product_id']} {position['side']}\n\n"
        f"Entry: {fmt_num(position['entry_price'], 6)}\n"
        f"Current: {fmt_num(current_price, 6)}\n\n"
        f"Margin Used: ${fmt_num(position['margin_used'], 2)}\n"
        f"Notional: ${fmt_num(position['notional'], 2)}\n\n"
        f"PnL: ${fmt_num(pnl, 2)} ({pnl_pct:+.2f}%)\n"
        f"Peak PnL: ${fmt_num(position['peak_pnl'], 2)} ({position['peak_pnl_pct']:+.2f}%)\n\n"
        f"Stop Loss: {fmt_num(position['stop_loss'], 6)}\n"
        f"Take Profit: {fmt_num(position['take_profit'], 6)}\n"
        f"Trailing Stop: {fmt_num(position['trailing_stop'], 6) if position['trailing_stop'] is not None else 'off'}\n"
        f"Highest: {fmt_num(position['highest_price'], 6)}\n"
        f"Lowest: {fmt_num(position['lowest_price'], 6)}"
    )


def build_close_alert(closed: Dict) -> str:
    side = closed["side"]
    pnl = closed["realized_pnl"]
    pnl_pct = closed["realized_pnl_pct"]
    icon = "✅" if pnl >= 0 else "❌"

    return (
        f"{icon} PAPER TRADE CLOSED\n\n"
        f"{closed['product_id']} {side}\n\n"
        f"Entry: {fmt_num(closed['entry_price'], 6)}\n"
        f"Exit: {fmt_num(closed['exit_price'], 6)}\n\n"
        f"Margin Used: ${fmt_num(closed['margin_used'], 2)}\n"
        f"Notional: ${fmt_num(closed['notional'], 2)}\n\n"
        f"Realized PnL: ${fmt_num(pnl, 2)} ({pnl_pct:+.2f}%)\n"
        f"Peak PnL: ${fmt_num(closed['peak_pnl'], 2)} ({closed['peak_pnl_pct']:+.2f}%)\n"
        f"Reason: {closed['exit_reason']}\n\n"
        f"Cash: ${fmt_num(PAPER_STATE['cash'], 2)}\n"
        f"Realized Total: ${fmt_num(PAPER_STATE['realized_pnl'], 2)}"
    )


def build_portfolio_summary(mark_prices: Dict[str, float]) -> str:
    equity = current_equity(mark_prices)
    open_pnl = total_open_pnl(mark_prices)
    starting = PAPER_STATE["starting_balance"]
    total_pnl = equity - starting
    total_pnl_pct = ((equity - starting) / starting * 100.0) if starting > 0 else 0.0

    lines = [
        "📊 PAPER PORTFOLIO SUMMARY",
        "",
        f"Starting Balance: ${fmt_num(starting, 2)}",
        f"Cash: ${fmt_num(PAPER_STATE['cash'], 2)}",
        f"Open PnL: ${fmt_num(open_pnl, 2)}",
        f"Realized PnL: ${fmt_num(PAPER_STATE['realized_pnl'], 2)}",
        f"Equity: ${fmt_num(equity, 2)}",
        f"Total PnL: ${fmt_num(total_pnl, 2)} ({total_pnl_pct:+.2f}%)",
        "",
        f"Open Trades: {len(OPEN_POSITIONS)}/{MAX_OPEN_TRADES}",
        f"Closed Trades: {PAPER_STATE['closed_trades']}",
        f"Wins: {PAPER_STATE['wins']}",
        f"Losses: {PAPER_STATE['losses']}",
    ]

    if OPEN_POSITIONS:
        lines.append("")
        lines.append("Open Positions:")
        for product_id, pos in OPEN_POSITIONS.items():
            current_price = mark_prices.get(product_id, pos["entry_price"])
            pnl, pnl_pct = mark_position_pnl(pos, current_price)
            lines.append(
                f"• {product_id} {pos['side']} | "
                f"Entry {fmt_num(pos['entry_price'], 6)} | "
                f"Now {fmt_num(current_price, 6)} | "
                f"PnL ${fmt_num(pnl, 2)} ({pnl_pct:+.2f}%)"
            )

    return "\n".join(lines)


# ------------------------------------------------------------
# COOLDOWN / DEDUPE
# ------------------------------------------------------------
def should_alert(product_id: str, direction: str) -> bool:
    key = f"{product_id}:{direction}"
    now_ts = utc_now_ts()
    last_ts = LAST_ALERT_AT.get(key, 0)
    cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60

    if now_ts - last_ts >= cooldown_seconds:
        LAST_ALERT_AT[key] = now_ts
        return True
    return False


# ------------------------------------------------------------
# STARTUP / MAIN
# ------------------------------------------------------------
def startup_message() -> str:
    return (
        "🤖 Coinbase Futures Strong-Move Paper Trader Started\n\n"
        f"Paper Trading: {'ON' if PAPER_TRADING else 'OFF'}\n"
        f"Products: {', '.join(FUTURES_PRODUCTS)}\n"
        f"Granularity: {GRANULARITY}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n"
        f"Score Threshold: {ALERT_SCORE_THRESHOLD}\n"
        f"Cooldown: {ALERT_COOLDOWN_MINUTES}m\n"
        f"Start Balance: ${fmt_num(START_BALANCE, 2)}\n"
        f"Max Open Trades: {MAX_OPEN_TRADES}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"SL: {STOP_LOSS_PCT:.2f}% | TP: {TAKE_PROFIT_PCT:.2f}% | "
        f"Trailing: {'ON' if TRAILING_STOP_ENABLED else 'OFF'} ({TRAILING_STOP_PCT:.2f}%)"
    )


def main() -> None:
    global SCAN_COUNT

    log("Starting Coinbase futures strong-move paper trader...")
    send_telegram(startup_message())

    while True:
        try:
            SCAN_COUNT += 1
            latest_marks: Dict[str, float] = {}

            for product_id in FUTURES_PRODUCTS:
                if not is_futures_product(product_id):
                    if DEBUG:
                        log(f"Skipping non-futures product: {product_id}")
                    continue

                signal = analyze_product(product_id)
                if not signal:
                    continue

                latest_marks[product_id] = signal["price"]

                # 1) Manage existing position first
                closed = manage_open_position(signal)
                if closed:
                    send_telegram(build_close_alert(closed))
                    log(f"CLOSED: {product_id} {closed['side']} pnl={closed['realized_pnl']:.2f}")
                    continue

                # 2) Send periodic position update
                if ENABLE_POSITION_UPDATES and product_id in OPEN_POSITIONS:
                    if should_send_position_update(product_id):
                        send_telegram(build_position_update(OPEN_POSITIONS[product_id], signal["price"]))

                # 3) If no open position, consider entry
                if PAPER_TRADING and product_id not in OPEN_POSITIONS:
                    # optional signal alert
                    if should_alert(signal["product_id"], signal["direction"]):
                        send_telegram(build_signal_alert(signal))
                        log(f"SIGNAL: {signal['product_id']} {signal['direction']} score={signal['score']}")

                    position = open_paper_position(signal)
                    if position:
                        send_telegram(build_entry_alert(position, signal))
                        log(f"OPENED: {product_id} {position['side']} entry={position['entry_price']}")

            # include open position prices not seen this loop
            for pid, pos in OPEN_POSITIONS.items():
                if pid not in latest_marks:
                    latest_marks[pid] = pos["entry_price"]

            if SUMMARY_EVERY_N_SCANS > 0 and (SCAN_COUNT % SUMMARY_EVERY_N_SCANS == 0):
                send_telegram(build_portfolio_summary(latest_marks))

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log("Stopped by user.")
            break
        except Exception as e:
            log(f"Main loop error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
    
