import os
import time
import json
import requests
from collections import deque
from typing import Dict, Optional, Tuple, Any

# ============================================================
# COINBASE SPOT ACCUMULATION SCANNER + PAPER TRADER + SIMPLE ML
# UPGRADED VERSION
# ============================================================
# - Coinbase spot products only
# - Paper trading only
# - Early accumulation detection
# - Pullback entry logic
# - Near-high trap filter
# - Optional add-on-breakout
# - Trailing stop
# - Telegram alerts
# - Forced Telegram status update
# - Simple self-learning trade memory
# - Balance persistence
# - Re-entry cooldown after exits
# ============================================================

# ================= CONFIG =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "180"))

START_BALANCE = float(os.getenv("START_BALANCE", "500"))
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "50"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "5"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.05"))          # 5%
STOP_LOSS = float(os.getenv("STOP_LOSS", "0.03"))              # 3%
TRAILING_ARM = float(os.getenv("TRAILING_ARM", "0.025"))       # 2.5%
TRAILING_STOP = float(os.getenv("TRAILING_STOP", "0.015"))     # 1.5%
BREAKOUT_ADD_ON_PCT = float(os.getenv("BREAKOUT_ADD_ON_PCT", "0.01"))  # 1%
ENABLE_ADD_ON_BREAKOUT = os.getenv("ENABLE_ADD_ON_BREAKOUT", "true").strip().lower() == "true"

ML_MIN_SCORE = float(os.getenv("ML_MIN_SCORE", "0.65"))
ML_MIN_SAMPLES = int(os.getenv("ML_MIN_SAMPLES", "10"))

REENTRY_COOLDOWN_SECONDS = int(os.getenv("REENTRY_COOLDOWN_SECONDS", "1800"))  # 30 min
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

STATE_FILE = os.getenv("STATE_FILE", "state.json")
ML_FILE = os.getenv("ML_FILE", "ml_data.json")
POSITIONS_FILE = os.getenv("POSITIONS_FILE", "positions.json")
TRADE_HISTORY_FILE = os.getenv("TRADE_HISTORY_FILE", "trade_history.json")

PRODUCTS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "BCH-USD",
    "ATOM-USD", "APT-USD", "ARB-USD", "OP-USD", "INJ-USD",
    "NEAR-USD", "FIL-USD", "SUI-USD", "SEI-USD", "PEPE-USD",
    "BONK-USD", "WIF-USD"
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Coinbase-ML-Scanner/2.0"})

# ================= STATE =================

balance = START_BALANCE
positions: Dict[str, dict] = {}
trade_history = []
ml_data = []
last_exit_times: Dict[str, int] = {}

price_history = {p: deque(maxlen=MAX_HISTORY) for p in PRODUCTS}
volume_history = {p: deque(maxlen=MAX_HISTORY) for p in PRODUCTS}

last_update = time.time()

# ================= FILE HELPERS =================

def load_json_file(path: str, default: Any):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json_file(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Failed saving {path}: {e}")

def load_state() -> None:
    global balance, positions, trade_history, ml_data, last_exit_times

    state = load_json_file(STATE_FILE, {})
    if isinstance(state, dict):
        balance = float(state.get("balance", START_BALANCE))
        last_exit_times_loaded = state.get("last_exit_times", {})
        if isinstance(last_exit_times_loaded, dict):
            last_exit_times = {
                str(k): int(v) for k, v in last_exit_times_loaded.items()
            }

    positions_loaded = load_json_file(POSITIONS_FILE, {})
    if isinstance(positions_loaded, dict):
        positions.update(positions_loaded)

    trade_history_loaded = load_json_file(TRADE_HISTORY_FILE, [])
    if isinstance(trade_history_loaded, list):
        trade_history.extend(trade_history_loaded)

    ml_loaded = load_json_file(ML_FILE, [])
    if isinstance(ml_loaded, list):
        ml_data.extend(ml_loaded)

    # Normalize positions
    for product, pos in list(positions.items()):
        if not isinstance(pos, dict):
            positions.pop(product, None)
            continue

        pos["entry"] = float(pos.get("entry", 0.0))
        pos["size"] = float(pos.get("size", TRADE_SIZE))
        pos["peak"] = float(pos.get("peak", pos["entry"]))
        pos["features"] = pos.get("features", {})
        pos["added_on_breakout"] = bool(pos.get("added_on_breakout", False))
        pos["opened_at"] = int(pos.get("opened_at", int(time.time())))
        pos["ml_score"] = float(pos.get("ml_score", 0.5))
        pos["trail_armed"] = bool(pos.get("trail_armed", False))
        pos["trail_stop_price"] = float(pos.get("trail_stop_price", 0.0))

def save_state() -> None:
    save_json_file(STATE_FILE, {
        "balance": balance,
        "last_exit_times": last_exit_times
    })
    save_json_file(POSITIONS_FILE, positions)
    save_json_file(TRADE_HISTORY_FILE, trade_history)
    save_json_file(ML_FILE, ml_data)

# ================= TELEGRAM =================

def send(msg: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(msg)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg
    }

    try:
        r = SESSION.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Telegram send failed: {e}")

# ================= COINBASE DATA =================

def get_candle(product: str) -> Optional[Tuple[float, float]]:
    """
    Returns (close_price, volume) from the latest candle.
    Uses Coinbase Advanced Trade market candles endpoint.
    """
    url = f"https://api.coinbase.com/api/v3/brokerage/market/products/{product}/candles"
    params = {
        "granularity": "FIVE_MINUTE",
        "limit": 20
    }

    try:
        r = SESSION.get(url, params=params, timeout=20)
        if r.status_code != 200:
            print(f"Coinbase candle error {product}: {r.status_code} {r.text}")
            return None

        payload = r.json()
        candles = payload.get("candles", [])
        if not candles:
            return None

        # Coinbase sometimes returns candles not guaranteed sorted
        candles_sorted = sorted(candles, key=lambda x: int(x.get("start", 0)))
        latest = candles_sorted[-1]

        close_price = float(latest["close"])
        volume = float(latest["volume"])

        return close_price, volume

    except Exception as e:
        print(f"Failed to fetch candle for {product}: {e}")
        return None

# ================= SIMPLE ML =================

def ml_score(features: Dict[str, float]) -> float:
    """
    Returns score from 0 to 1.
    0.5 = neutral when there is not enough data.
    """
    if len(ml_data) < ML_MIN_SAMPLES:
        return 0.60

    weighted_sum = 0.0
    weight_total = 0.0

    for row in ml_data:
        past_features = row.get("features", {})
        result = row.get("result", 0)

        if not past_features:
            continue

        similarity_parts = []
        for key, value in features.items():
            past_value = float(past_features.get(key, 0.0))
            diff = abs(float(value) - past_value)
            similarity = max(0.0, 1.0 - diff)
            similarity_parts.append(similarity)

        if not similarity_parts:
            continue

        similarity = sum(similarity_parts) / len(similarity_parts)
        outcome_score = 1.0 if result > 0 else 0.0

        weighted_sum += similarity * outcome_score
        weight_total += similarity

    if weight_total == 0:
        return 0.5

    return weighted_sum / weight_total

def log_trade(
    features: Dict[str, float],
    pnl_pct: float,
    product: str,
    entry: float,
    exit_price: float,
    reason: str,
    size: float,
    profit: float,
    ml_score_value: float
) -> None:
    result = 1 if pnl_pct > 0 else -1
    ts = int(time.time())

    ml_data.append({
        "features": features,
        "result": result,
        "pnl_pct": pnl_pct,
        "product": product,
        "entry": entry,
        "exit": exit_price,
        "reason": reason,
        "size": size,
        "profit": profit,
        "ml_score": ml_score_value,
        "ts": ts
    })

    trade_history.append({
        "product": product,
        "entry": entry,
        "exit": exit_price,
        "pnl_pct": pnl_pct,
        "reason": reason,
        "size": size,
        "profit": profit,
        "ml_score": ml_score_value,
        "ts": ts
    })

    # Keep files from growing forever
    if len(ml_data) > 2000:
        del ml_data[:-2000]
    if len(trade_history) > 2000:
        del trade_history[:-2000]

    save_state()

# ================= SIGNAL FEATURES =================

def extract_features(product: str) -> Optional[Dict[str, float]]:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 10 or len(vols) < 10:
        return None

    avg_price = sum(prices) / len(prices)
    if avg_price <= 0:
        return None

    price_range = max(prices) - min(prices)
    volatility = price_range / avg_price

    avg_old_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    vol_trend = vols[-1] / avg_old_vol if avg_old_vol > 0 else 0.0

    drift = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0
    pullback_from_high = (max(prices[:-1]) - prices[-1]) / max(prices[:-1]) if max(prices[:-1]) > 0 else 0.0
    proximity_to_high = prices[-1] / max(prices) if max(prices) > 0 else 1.0

    return {
        "volatility": max(0.0, min(volatility, 1.0)),
        "vol_trend": max(0.0, min(vol_trend / 4.0, 1.0)),
        "drift": max(0.0, min((drift + 0.10) / 0.20, 1.0)),
        "pullback": max(0.0, min(pullback_from_high / 0.05, 1.0)),
        "high_proximity": max(0.0, min(proximity_to_high, 1.0)),
    }

def near_high_filter(product: str) -> bool:
    prices = list(price_history[product])
    if len(prices) < 10:
        return False

    recent_high = max(prices)
    current = prices[-1]
    return current >= recent_high * 0.985

def pullback_entry_ok(product: str) -> bool:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 10 or len(vols) < 10:
        return False

    current = prices[-1]
    recent_high = max(prices[:-1])
    recent_low = min(prices[-5:])

    if recent_high <= 0:
        return False

    pullback_pct = (recent_high - current) / recent_high
    bounce_from_low = (current - recent_low) / recent_low if recent_low > 0 else 0.0

    # Want a pullback, but not a full dump
    if pullback_pct < 0.003:
        return False
    if pullback_pct > 0.02:
        return False

    # Some sign price is lifting off local low
    if bounce_from_low < 0.001:
        return False

    return True

def is_accumulation(product: str) -> bool:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 10 or len(vols) < 10:
        return False

    avg_price = sum(prices) / len(prices)
    if avg_price <= 0:
        return False

    price_range_pct = (max(prices) - min(prices)) / avg_price
    drift_pct = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0

    avg_old_vol = sum(vols[:-3]) / max(1, len(vols[:-3])) if len(vols) > 3 else 0.0
    recent_avg_vol = sum(vols[-3:]) / 3 if len(vols) >= 3 else 0.0

    # Tight range
    if price_range_pct > 0.018:
        return False

    # Not already extended
    if abs(drift_pct) > 0.012:
        return False

    # Some volume improvement
    if avg_old_vol > 0 and recent_avg_vol < avg_old_vol * 1.10:
        return False

    return True

def is_breakout(product: str) -> bool:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 10 or len(vols) < 10:
        return False

    last_price = prices[-1]
    prior_high = max(prices[:-1])

    if last_price <= prior_high * (1.0 + BREAKOUT_ADD_ON_PCT):
        return False

    avg_prior_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    if avg_prior_vol <= 0:
        return False

    if vols[-1] < avg_prior_vol * 1.75:
        return False

    return True

def cooldown_active(product: str) -> bool:
    ts = last_exit_times.get(product, 0)
    if ts <= 0:
        return False
    return (time.time() - ts) < REENTRY_COOLDOWN_SECONDS

# ================= TRADING =================

def open_trade(product: str, price: float, features: Dict[str, float]) -> None:
    global balance

    if product in positions:
        return

    if len(positions) >= MAX_OPEN_TRADES:
        return

    if balance < TRADE_SIZE:
        return

    if cooldown_active(product):
        return

    if near_high_filter(product):
        return

    if not pullback_entry_ok(product):
        return

    score = ml_score(features)

    if len(ml_data) >= ML_MIN_SAMPLES and score < ML_MIN_SCORE:
        return

    balance -= TRADE_SIZE

    positions[product] = {
        "entry": price,
        "size": TRADE_SIZE,
        "peak": price,
        "features": features,
        "added_on_breakout": False,
        "opened_at": int(time.time()),
        "ml_score": round(score, 4),
        "trail_armed": False,
        "trail_stop_price": 0.0
    }
    save_state()

    send(
        f"🤖 MACHINE LEARNING ON\n"
        f"🟡 ENTRY {product}\n"
        f"Price: {price:.6f}\n"
        f"ML Score: {score:.2f}\n"
        f"Paper Size: ${TRADE_SIZE:.2f}\n"
        f"Balance: ${balance:.2f}"
    )

def add_trade(product: str, price: float) -> None:
    global balance

    if product not in positions:
        return

    pos = positions[product]

    if pos.get("added_on_breakout"):
        return

    if balance < TRADE_SIZE:
        return

    entry = float(pos["entry"])
    current_gain = (price - entry) / entry if entry > 0 else 0.0

    # Don't add unless trade is already working
    if current_gain < 0.01:
        return

    balance -= TRADE_SIZE
    pos["size"] = float(pos["size"]) + TRADE_SIZE
    pos["peak"] = max(float(pos.get("peak", price)), price)
    pos["added_on_breakout"] = True
    save_state()

    send(
        f"🤖 MACHINE LEARNING ON\n"
        f"🚀 ADD ON BREAKOUT {product}\n"
        f"Price: {price:.6f}\n"
        f"New Position Size: ${float(pos['size']):.2f}\n"
        f"Balance: ${balance:.2f}"
    )

def close_trade(product: str, price: float, reason: str) -> None:
    global balance

    pos = positions.pop(product, None)
    if not pos:
        return

    entry = float(pos["entry"])
    size = float(pos["size"])
    features = pos.get("features", {})
    score = float(pos.get("ml_score", 0.5))

    pnl_pct = (price - entry) / entry if entry > 0 else 0.0
    profit = size * pnl_pct

    balance += size + profit
    last_exit_times[product] = int(time.time())
    save_state()

    log_trade(features, pnl_pct, product, entry, price, reason, size, profit, score)

    send(
        f"🤖 MACHINE LEARNING ON\n"
        f"🔴 EXIT {product} ({reason})\n"
        f"Entry: {entry:.6f}\n"
        f"Exit: {price:.6f}\n"
        f"PnL: ${profit:.2f} ({pnl_pct * 100:.2f}%)\n"
        f"Balance: ${balance:.2f}"
    )

def manage_position(product: str, price: float) -> None:
    if product not in positions:
        return

    pos = positions[product]
    entry = float(pos["entry"])
    peak = max(float(pos.get("peak", price)), price)
    pos["peak"] = peak

    change = (price - entry) / entry if entry > 0 else 0.0
    drawdown_from_peak = (peak - price) / peak if peak > 0 else 0.0

    # Fixed TP first
    if change >= TAKE_PROFIT:
        close_trade(product, price, "TP")
        return

    # Fixed SL
    if change <= -STOP_LOSS:
        close_trade(product, price, "SL")
        return

    # Arm trailing stop after enough profit
    if not pos.get("trail_armed", False) and change >= TRAILING_ARM:
        pos["trail_armed"] = True
        pos["trail_stop_price"] = peak * (1.0 - TRAILING_STOP)
        save_state()

        send(
            f"🤖 MACHINE LEARNING ON\n"
            f"🟦 TRAILING ARMED {product}\n"
            f"Entry: {entry:.6f}\n"
            f"Peak: {peak:.6f}\n"
            f"Trail Stop: {float(pos['trail_stop_price']):.6f}"
        )

    # Update trailing stop if armed
    if pos.get("trail_armed", False):
        new_trail = peak * (1.0 - TRAILING_STOP)
        if new_trail > float(pos.get("trail_stop_price", 0.0)):
            pos["trail_stop_price"] = new_trail
            save_state()

        if price <= float(pos.get("trail_stop_price", 0.0)):
            close_trade(product, price, "TRAIL")
            return

    # Save peak updates periodically
    save_state()

# ================= STATUS =================

def send_update() -> None:
    total_open_value = 0.0
    total_open_pnl = 0.0

    lines = [
        "🤖 MACHINE LEARNING ON",
        "📊 3-MIN UPDATE",
        f"Balance: ${balance:.2f}",
        f"Open Trades: {len(positions)}",
        f"ML Samples: {len(ml_data)}",
        ""
    ]

    if positions:
        for product, pos in positions.items():
            prices = list(price_history[product])
            current_price = prices[-1] if prices else float(pos["entry"])
            entry = float(pos["entry"])
            size = float(pos["size"])
            value = size * (current_price / entry) if entry > 0 else size
            pnl = value - size
            pnl_pct = ((current_price - entry) / entry * 100) if entry > 0 else 0.0

            total_open_value += value
            total_open_pnl += pnl

            trail_text = ""
            if pos.get("trail_armed", False):
                trail_text = f" | Trail {float(pos.get('trail_stop_price', 0.0)):.6f}"

            lines.append(
                f"{product} | Entry {entry:.6f} | Now {current_price:.6f} | "
                f"PnL ${pnl:.2f} ({pnl_pct:.2f}%) | "
                f"Size ${size:.2f} | ML {float(pos.get('ml_score', 0.5)):.2f}{trail_text}"
            )

        lines.extend([
            "",
            f"Open Position Value: ${total_open_value:.2f}",
            f"Open Position PnL: ${total_open_pnl:.2f}"
        ])
    else:
        lines.append("No open positions.")

    send("\n".join(lines))

# ================= MAIN =================

load_state()
send("🤖 MACHINE LEARNING ON\n🚀 SCANNER STARTED")

while True:
    try:
        for product in PRODUCTS:
            candle = get_candle(product)
            if not candle:
                continue

            price, volume = candle

            price_history[product].append(price)
            volume_history[product].append(volume)

            if product in positions:
                positions[product]["peak"] = max(
                    float(positions[product].get("peak", price)),
                    price
                )

            features = extract_features(product)
            if not features:
                continue

            if product not in positions and is_accumulation(product):
                open_trade(product, price, features)

            if ENABLE_ADD_ON_BREAKOUT and product in positions and is_breakout(product):
                add_trade(product, price)

            if product in positions:
                manage_position(product, price)

        if time.time() - last_update >= UPDATE_INTERVAL:
            send_update()
            last_update = time.time()

        time.sleep(SCAN_INTERVAL)

    except Exception as e:
        print(f"Main loop error: {e}")
        time.sleep(5)
