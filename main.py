import os
import time
import json
import requests
from collections import deque
from typing import Dict, Optional, Tuple

# ============================================================
# COINBASE SPOT ACCUMULATION SCANNER + PAPER TRADER + SIMPLE ML
# ============================================================
# - Coinbase spot products only
# - Paper trading only
# - Early accumulation detection
# - Optional add-on-breakout
# - Telegram alerts
# - Forced Telegram status update every 3 minutes
# - Simple self-learning trade memory
# ============================================================

# ================= CONFIG =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
UPDATE_INTERVAL = 180  # 3 minutes

START_BALANCE = float(os.getenv("START_BALANCE", "500"))
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "50"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "5"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.05"))   # 5%
STOP_LOSS = float(os.getenv("STOP_LOSS", "0.03"))       # 3%

ENABLE_ADD_ON_BREAKOUT = os.getenv("ENABLE_ADD_ON_BREAKOUT", "true").strip().lower() == "true"

# ML threshold: skip trades below this score once enough data exists
ML_MIN_SCORE = float(os.getenv("ML_MIN_SCORE", "0.55"))

# History files
ML_FILE = os.getenv("ML_FILE", "ml_data.json")
POSITIONS_FILE = os.getenv("POSITIONS_FILE", "positions.json")
TRADE_HISTORY_FILE = os.getenv("TRADE_HISTORY_FILE", "trade_history.json")

# Coinbase spot pairs only
PRODUCTS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "BCH-USD",
    "ATOM-USD", "APT-USD", "ARB-USD", "OP-USD", "INJ-USD",
    "NEAR-USD", "FIL-USD", "SUI-USD", "SEI-USD", "PEPE-USD",
    "BONK-USD", "WIF-USD"
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Coinbase-ML-Scanner/1.0"})

# ================= STATE =================

balance = START_BALANCE
positions: Dict[str, dict] = {}

# We only need a short rolling window
price_history = {p: deque(maxlen=10) for p in PRODUCTS}
volume_history = {p: deque(maxlen=10) for p in PRODUCTS}

last_update = time.time()
last_status_sent = {}
trade_history = []

# ================= FILE HELPERS =================

def load_json_file(path: str, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json_file(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Failed saving {path}: {e}")

# Load ML and prior state
ml_data = load_json_file(ML_FILE, [])
positions = load_json_file(POSITIONS_FILE, {})
trade_history = load_json_file(TRADE_HISTORY_FILE, [])

# If positions loaded from disk, normalize their structure
for product, pos in list(positions.items()):
    if not isinstance(pos, dict):
        positions.pop(product, None)
        continue
    pos.setdefault("entry", 0.0)
    pos.setdefault("size", TRADE_SIZE)
    pos.setdefault("peak", pos.get("entry", 0.0))
    pos.setdefault("features", {})

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
        "limit": 5
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

        latest = candles[-1]

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
    if len(ml_data) < 5:
        return 0.5

    weighted_sum = 0.0
    weight_total = 0.0

    for row in ml_data:
        past_features = row.get("features", {})
        result = row.get("result", 0)  # 1 for win, -1 for loss

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

        # Convert result -1/1 into 0/1 style contribution
        outcome_score = 1.0 if result > 0 else 0.0

        weighted_sum += similarity * outcome_score
        weight_total += similarity

    if weight_total == 0:
        return 0.5

    return weighted_sum / weight_total

def log_trade(features: Dict[str, float], pnl_pct: float, product: str, entry: float, exit_price: float, reason: str) -> None:
    result = 1 if pnl_pct > 0 else -1

    ml_data.append({
        "features": features,
        "result": result,
        "pnl_pct": pnl_pct,
        "product": product,
        "entry": entry,
        "exit": exit_price,
        "reason": reason,
        "ts": int(time.time())
    })
    save_json_file(ML_FILE, ml_data)

    trade_history.append({
        "product": product,
        "entry": entry,
        "exit": exit_price,
        "pnl_pct": pnl_pct,
        "reason": reason,
        "ts": int(time.time())
    })
    save_json_file(TRADE_HISTORY_FILE, trade_history)

# ================= SIGNAL FEATURES =================

def extract_features(product: str) -> Optional[Dict[str, float]]:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 5 or len(vols) < 5:
        return None

    avg_price = sum(prices) / len(prices)
    price_range = max(prices) - min(prices)
    volatility = (price_range / avg_price) if avg_price > 0 else 0.0

    avg_old_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    vol_trend = vols[-1] / avg_old_vol if avg_old_vol > 0 else 0.0

    # Price drift over window
    drift = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0

    # Normalize rough ranges into 0..1
    return {
        "volatility": max(0.0, min(volatility, 1.0)),
        "vol_trend": max(0.0, min(vol_trend / 3.0, 1.0)),
        "drift": max(0.0, min((drift + 0.05) / 0.10, 1.0)),
    }

def is_accumulation(product: str) -> bool:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 5 or len(vols) < 5:
        return False

    avg_price = sum(prices) / len(prices)
    if avg_price <= 0:
        return False

    price_range_pct = (max(prices) - min(prices)) / avg_price

    # Tight range
    if price_range_pct > 0.02:
        return False

    # Gentle rising volume, not necessarily perfectly monotonic
    rising_steps = 0
    for i in range(len(vols) - 1):
        if vols[i + 1] >= vols[i]:
            rising_steps += 1

    if rising_steps < 3:
        return False

    # Avoid already-large breakout drift
    drift_pct = abs((prices[-1] - prices[0]) / prices[0]) if prices[0] > 0 else 0.0
    if drift_pct > 0.02:
        return False

    return True

def is_breakout(product: str) -> bool:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 5 or len(vols) < 5:
        return False

    last_price = prices[-1]
    prior_high = max(prices[:-1])

    if last_price <= prior_high:
        return False

    avg_prior_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    if avg_prior_vol <= 0:
        return False

    if vols[-1] < avg_prior_vol * 1.5:
        return False

    return True

# ================= TRADING =================

def persist_positions() -> None:
    save_json_file(POSITIONS_FILE, positions)

def open_trade(product: str, price: float, features: Dict[str, float]) -> None:
    global balance

    if product in positions:
        return

    if len(positions) >= MAX_OPEN_TRADES:
        return

    if balance < TRADE_SIZE:
        return

    score = ml_score(features)

    # Only enforce ML filter once enough samples exist
    if len(ml_data) >= 5 and score < ML_MIN_SCORE:
        return

    balance -= TRADE_SIZE

    positions[product] = {
        "entry": price,
        "size": TRADE_SIZE,
        "peak": price,
        "features": features,
        "added_on_breakout": False,
        "opened_at": int(time.time()),
        "ml_score": round(score, 4)
    }
    persist_positions()

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

    balance -= TRADE_SIZE
    pos["size"] += TRADE_SIZE
    pos["peak"] = max(float(pos.get("peak", price)), price)
    pos["added_on_breakout"] = True
    persist_positions()

    send(
        f"🤖 MACHINE LEARNING ON\n"
        f"🚀 ADD ON BREAKOUT {product}\n"
        f"Price: {price:.6f}\n"
        f"New Position Size: ${pos['size']:.2f}\n"
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

    pnl_pct = (price - entry) / entry if entry > 0 else 0.0
    profit = size * pnl_pct

    balance += size + profit
    persist_positions()
    log_trade(features, pnl_pct, product, entry, price, reason)

    send(
        f"🤖 MACHINE LEARNING ON\n"
        f"🔴 EXIT {product} ({reason})\n"
        f"Entry: {entry:.6f}\n"
        f"Exit: {price:.6f}\n"
        f"PnL: ${profit:.2f} ({pnl_pct * 100:.2f}%)\n"
        f"Balance: ${balance:.2f}"
    )

# ================= STATUS =================

def send_update() -> None:
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
            lines.append(
                f"{product} | Entry {float(pos['entry']):.6f} | "
                f"Size ${float(pos['size']):.2f} | "
                f"ML {float(pos.get('ml_score', 0.5)):.2f}"
            )
    else:
        lines.append("No open positions.")

    send("\n".join(lines))

# ================= MAIN =================

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

            # Update peak for open positions
            if product in positions:
                positions[product]["peak"] = max(float(positions[product].get("peak", price)), price)

            features = extract_features(product)
            if not features:
                continue

            if product not in positions and is_accumulation(product):
                open_trade(product, price, features)

            if ENABLE_ADD_ON_BREAKOUT and product in positions and is_breakout(product):
                add_trade(product, price)

            if product in positions:
                entry = float(positions[product]["entry"])
                change = (price - entry) / entry if entry > 0 else 0.0

                if change >= TAKE_PROFIT:
                    close_trade(product, price, "TP")
                    continue

                if change <= -STOP_LOSS:
                    close_trade(product, price, "SL")
                    continue

        if time.time() - last_update >= UPDATE_INTERVAL:
            send_update()
            last_update = time.time()

        time.sleep(SCAN_INTERVAL)

    except Exception as e:
        print(f"Main loop error: {e}")
        time.sleep(5)
