import os
import time
import json
import math
import secrets
import requests
import jwt
from collections import deque
from typing import Dict, Optional, Tuple, Any

# ============================================================
# COINBASE SPOT ACCUMULATION SCANNER + PAPER TRADER + SIMPLE ML
# LIVE-READY VERSION (DEFAULTS TO PAPER MODE)
# ============================================================
# - Coinbase spot products only
# - Paper trading by default
# - Live trading ready via RUN_LIVE_TRADING=true
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
# - Entry filters controlled by env vars
# - Full running performance stats
# - ML stays OFF until enough closed trades exist
# ============================================================

# ================= CONFIG =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "180"))

START_BALANCE = float(os.getenv("START_BALANCE", "500"))
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "50"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "5"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.05"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "0.03"))

TRAILING_ARM = float(os.getenv("TRAILING_ARM", "0.02"))
TRAILING_STOP = float(os.getenv("TRAILING_STOP", "0.02"))

BREAKOUT_ADD_ON_PCT = float(os.getenv("BREAKOUT_ADD_ON_PCT", "0.005"))
ENABLE_ADD_ON_BREAKOUT = os.getenv("ENABLE_ADD_ON_BREAKOUT", "true").strip().lower() == "true"

ML_MIN_SCORE = float(os.getenv("ML_MIN_SCORE", "0.58"))
ML_MIN_SAMPLES = int(os.getenv("ML_MIN_SAMPLES", "6"))
ML_MIN_TRADES = int(os.getenv("ML_MIN_TRADES", "200"))

REENTRY_COOLDOWN_SECONDS = int(os.getenv("REENTRY_COOLDOWN_SECONDS", "900"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

# Entry filters
MIN_FEATURE_HISTORY = int(os.getenv("MIN_FEATURE_HISTORY", "8"))
NEAR_HIGH_BLOCK_PCT = float(os.getenv("NEAR_HIGH_BLOCK_PCT", "0.995"))
MAX_ACCUM_RANGE = float(os.getenv("MAX_ACCUM_RANGE", "0.03"))
MAX_ACCUM_DRIFT = float(os.getenv("MAX_ACCUM_DRIFT", "0.02"))
MIN_VOLUME_IMPROVEMENT = float(os.getenv("MIN_VOLUME_IMPROVEMENT", "1.00"))
MIN_PULLBACK_PCT = float(os.getenv("MIN_PULLBACK_PCT", "0.0"))
MAX_PULLBACK_PCT = float(os.getenv("MAX_PULLBACK_PCT", "0.03"))
MIN_BOUNCE_FROM_LOW = float(os.getenv("MIN_BOUNCE_FROM_LOW", "0.0"))
BREAKOUT_VOLUME_MULT = float(os.getenv("BREAKOUT_VOLUME_MULT", "1.30"))
MIN_ADD_ON_GAIN = float(os.getenv("MIN_ADD_ON_GAIN", "0.005"))

# Mode
RUN_LIVE_TRADING = os.getenv("RUN_LIVE_TRADING", "false").strip().lower() == "true"

# Coinbase CDP / Advanced Trade auth
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "").strip()
COINBASE_API_PRIVATE_KEY = os.getenv("COINBASE_API_PRIVATE_KEY", "").replace("\\n", "\n").strip()

# Safety
LIVE_TRADING_REQUIRE_CONFIRM = os.getenv("LIVE_TRADING_REQUIRE_CONFIRM", "true").strip().lower() == "true"
MIN_CASH_BUFFER = float(os.getenv("MIN_CASH_BUFFER", "25"))
ORDER_TIMEOUT_SECONDS = int(os.getenv("ORDER_TIMEOUT_SECONDS", "20"))
ORDER_STATUS_POLL_SECONDS = float(os.getenv("ORDER_STATUS_POLL_SECONDS", "1.0"))
ORDER_STATUS_MAX_POLLS = int(os.getenv("ORDER_STATUS_MAX_POLLS", "12"))

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
SESSION.headers.update({"User-Agent": "Coinbase-ML-Scanner/4.1"})

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
            last_exit_times = {str(k): int(v) for k, v in last_exit_times_loaded.items()}

    positions_loaded = load_json_file(POSITIONS_FILE, {})
    if isinstance(positions_loaded, dict):
        positions.update(positions_loaded)

    trade_history_loaded = load_json_file(TRADE_HISTORY_FILE, [])
    if isinstance(trade_history_loaded, list):
        trade_history.extend(trade_history_loaded)

    ml_loaded = load_json_file(ML_FILE, [])
    if isinstance(ml_loaded, list):
        ml_data.extend(ml_loaded)

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
        pos["ml_score"] = float(pos.get("ml_score", 0.0))
        pos["ml_active_at_entry"] = bool(pos.get("ml_active_at_entry", False))
        pos["trail_armed"] = bool(pos.get("trail_armed", False))
        pos["trail_stop_price"] = float(pos.get("trail_stop_price", 0.0))

        pos["base_size"] = float(pos.get("base_size", 0.0))
        pos["live_order_id"] = str(pos.get("live_order_id", ""))
        pos["last_buy_fill_price"] = float(pos.get("last_buy_fill_price", pos["entry"]))
        pos["mode"] = str(pos.get("mode", "paper"))

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

    try:
        r = SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=15
        )
        if r.status_code != 200:
            print(f"Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Telegram send failed: {e}")

# ================= BASIC HELPERS =================

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default

def get_product_base_currency(product_id: str) -> str:
    if "-" in product_id:
        return product_id.split("-")[0]
    return product_id

def round_down(value: float, decimals: int = 8) -> float:
    if decimals < 0:
        return value
    factor = 10 ** decimals
    return math.floor(value * factor) / factor

def get_closed_trade_count() -> int:
    return len(trade_history)

def ml_is_active() -> bool:
    return get_closed_trade_count() >= ML_MIN_TRADES

def get_ml_status_text() -> str:
    closed = get_closed_trade_count()
    if ml_is_active():
        return f"ON ({closed}/{ML_MIN_TRADES}+)"
    return f"OFF ({closed}/{ML_MIN_TRADES})"

def format_ml_display(score: float, active: bool) -> str:
    return f"{score:.2f}" if active else "OFF"

# ================= PERFORMANCE HELPERS =================

def get_open_position_stats() -> Dict[str, float]:
    total_open_value = 0.0
    total_open_cost = 0.0
    total_open_pnl = 0.0

    for product, pos in positions.items():
        prices = list(price_history[product])
        current_price = prices[-1] if prices else float(pos.get("entry", 0.0))
        entry = float(pos.get("entry", 0.0))
        usd_size = float(pos.get("size", 0.0))
        base_size = float(pos.get("base_size", 0.0))

        if base_size > 0 and current_price > 0:
            value = base_size * current_price
        elif entry > 0 and current_price > 0:
            value = usd_size * (current_price / entry)
        else:
            value = usd_size

        pnl = value - usd_size

        total_open_value += value
        total_open_cost += usd_size
        total_open_pnl += pnl

    return {
        "open_value": total_open_value,
        "open_cost": total_open_cost,
        "open_pnl": total_open_pnl
    }

def get_closed_trade_stats() -> Dict[str, float]:
    realized_pnl = 0.0
    wins = 0
    losses = 0
    total_closed = 0
    gross_win = 0.0
    gross_loss = 0.0
    best_trade = None
    worst_trade = None

    for trade in trade_history:
        profit = float(trade.get("profit", 0.0))
        total_closed += 1
        realized_pnl += profit

        if best_trade is None or profit > best_trade:
            best_trade = profit
        if worst_trade is None or profit < worst_trade:
            worst_trade = profit

        if profit > 0:
            wins += 1
            gross_win += profit
        elif profit < 0:
            losses += 1
            gross_loss += profit
        else:
            losses += 1

    avg_win = gross_win / wins if wins > 0 else 0.0
    avg_loss = gross_loss / losses if losses > 0 else 0.0
    win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0

    return {
        "realized_pnl": realized_pnl,
        "wins": wins,
        "losses": losses,
        "total_closed": total_closed,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "best_trade": best_trade if best_trade is not None else 0.0,
        "worst_trade": worst_trade if worst_trade is not None else 0.0
    }

def get_account_stats() -> Dict[str, float]:
    open_stats = get_open_position_stats()
    closed_stats = get_closed_trade_stats()

    total_account_value = balance + open_stats["open_value"]
    total_pnl = total_account_value - START_BALANCE

    return {
        **open_stats,
        **closed_stats,
        "cash_balance": balance,
        "total_account_value": total_account_value,
        "total_pnl": total_pnl
    }

# ================= COINBASE AUTH =================

def build_jwt(method: str, path: str) -> str:
    if not COINBASE_API_KEY or not COINBASE_API_PRIVATE_KEY:
        raise ValueError("Missing COINBASE_API_KEY or COINBASE_API_PRIVATE_KEY")

    now = int(time.time())
    payload = {
        "sub": COINBASE_API_KEY,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uri": f"{method.upper()} api.coinbase.com{path}",
    }
    headers = {
        "kid": COINBASE_API_KEY,
        "nonce": secrets.token_hex(),
    }

    token = jwt.encode(
        payload,
        COINBASE_API_PRIVATE_KEY,
        algorithm="ES256",
        headers=headers
    )
    return token

def cb_request(method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> dict:
    jwt_token = build_jwt(method, path)
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
    }
    url = f"https://api.coinbase.com{path}"

    response = SESSION.request(
        method=method.upper(),
        url=url,
        headers=headers,
        params=params,
        json=body,
        timeout=ORDER_TIMEOUT_SECONDS
    )

    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": response.text}

    if response.status_code >= 400:
        raise RuntimeError(f"Coinbase API error {response.status_code}: {payload}")

    return payload

# ================= COINBASE LIVE ORDER HELPERS =================

def get_live_accounts() -> dict:
    return cb_request("GET", "/api/v3/brokerage/accounts")

def get_live_available_cash_usd() -> float:
    try:
        payload = get_live_accounts()
        accounts = payload.get("accounts", [])
        for account in accounts:
            currency = str(account.get("currency", "")).upper()
            if currency == "USD":
                available_balance = account.get("available_balance", {})
                return safe_float(available_balance.get("value", 0.0))
    except Exception as e:
        print(f"Failed to get live USD balance: {e}")
    return 0.0

def get_live_available_base(product: str) -> float:
    base_currency = get_product_base_currency(product).upper()
    try:
        payload = get_live_accounts()
        accounts = payload.get("accounts", [])
        for account in accounts:
            currency = str(account.get("currency", "")).upper()
            if currency == base_currency:
                available_balance = account.get("available_balance", {})
                return safe_float(available_balance.get("value", 0.0))
    except Exception as e:
        print(f"Failed to get live {base_currency} balance: {e}")
    return 0.0

def create_market_buy_order(product: str, quote_size_usd: float) -> dict:
    client_order_id = f"buy-{product}-{int(time.time()*1000)}"
    body = {
        "client_order_id": client_order_id,
        "product_id": product,
        "side": "BUY",
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": f"{quote_size_usd:.2f}"
            }
        }
    }
    return cb_request("POST", "/api/v3/brokerage/orders", body=body)

def create_market_sell_order(product: str, base_size: float) -> dict:
    client_order_id = f"sell-{product}-{int(time.time()*1000)}"
    sell_size = round_down(base_size, 8)

    body = {
        "client_order_id": client_order_id,
        "product_id": product,
        "side": "SELL",
        "order_configuration": {
            "market_market_ioc": {
                "base_size": f"{sell_size:.8f}"
            }
        }
    }
    return cb_request("POST", "/api/v3/brokerage/orders", body=body)

def get_order(order_id: str) -> dict:
    return cb_request("GET", f"/api/v3/brokerage/orders/historical/{order_id}")

def extract_order_id(order_response: dict) -> str:
    success_response = order_response.get("success_response", {})
    return str(success_response.get("order_id", "")).strip()

def wait_for_order_fill(order_id: str) -> dict:
    last_payload = {}
    for _ in range(ORDER_STATUS_MAX_POLLS):
        payload = get_order(order_id)
        last_payload = payload

        order = payload.get("order", {})
        status = str(order.get("status", "")).upper()

        if status in {"FILLED", "COMPLETED"}:
            return payload

        if status in {"FAILED", "CANCELLED", "EXPIRED"}:
            return payload

        time.sleep(ORDER_STATUS_POLL_SECONDS)

    return last_payload

def parse_filled_buy(order_payload: dict, fallback_price: float, quote_size: float) -> Tuple[float, float]:
    order = order_payload.get("order", {})

    avg_filled_price = safe_float(order.get("average_filled_price"), 0.0)
    filled_size = safe_float(order.get("filled_size"), 0.0)
    filled_value = safe_float(order.get("filled_value"), 0.0)

    if avg_filled_price <= 0 and filled_size > 0 and filled_value > 0:
        avg_filled_price = filled_value / filled_size

    if avg_filled_price <= 0:
        avg_filled_price = fallback_price

    if filled_size <= 0 and avg_filled_price > 0:
        filled_size = quote_size / avg_filled_price

    return avg_filled_price, filled_size

def parse_filled_sell(order_payload: dict, fallback_price: float, base_size: float) -> Tuple[float, float]:
    order = order_payload.get("order", {})

    avg_filled_price = safe_float(order.get("average_filled_price"), 0.0)
    filled_size = safe_float(order.get("filled_size"), 0.0)
    filled_value = safe_float(order.get("filled_value"), 0.0)

    if avg_filled_price <= 0 and filled_size > 0 and filled_value > 0:
        avg_filled_price = filled_value / filled_size

    if avg_filled_price <= 0:
        avg_filled_price = fallback_price

    if filled_size <= 0:
        filled_size = base_size

    return avg_filled_price, filled_size

def live_mode_ready() -> bool:
    if not RUN_LIVE_TRADING:
        return False
    return bool(COINBASE_API_KEY and COINBASE_API_PRIVATE_KEY)

# ================= COINBASE PUBLIC DATA =================

def get_candle(product: str) -> Optional[Tuple[float, float]]:
    url = f"https://api.coinbase.com/api/v3/brokerage/market/products/{product}/candles"
    params = {
        "granularity": "FIVE_MINUTE",
        "limit": MAX_HISTORY
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
    ml_score_value: float,
    ml_was_active: bool
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
        "ml_active": ml_was_active,
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
        "ml_active": ml_was_active,
        "ts": ts
    })

    if len(ml_data) > 2000:
        del ml_data[:-2000]
    if len(trade_history) > 2000:
        del trade_history[:-2000]

    save_state()

# ================= SIGNAL FEATURES =================

def extract_features(product: str) -> Optional[Dict[str, float]]:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < MIN_FEATURE_HISTORY or len(vols) < MIN_FEATURE_HISTORY:
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
    if len(prices) < MIN_FEATURE_HISTORY:
        return False

    recent_high = max(prices)
    current = prices[-1]
    return current >= recent_high * NEAR_HIGH_BLOCK_PCT

def pullback_entry_ok(product: str) -> bool:
    prices = list(price_history[product])
    if len(prices) < MIN_FEATURE_HISTORY:
        return True

    current = prices[-1]
    recent_high = max(prices[:-1])
    recent_low = min(prices[-5:]) if len(prices) >= 5 else min(prices)

    if recent_high <= 0 or recent_low <= 0:
        return False

    pullback_pct = (recent_high - current) / recent_high
    bounce_from_low = (current - recent_low) / recent_low

    if pullback_pct < MIN_PULLBACK_PCT:
        return False
    if pullback_pct > MAX_PULLBACK_PCT:
        return False
    if bounce_from_low < MIN_BOUNCE_FROM_LOW:
        return False

    return True

def is_accumulation(product: str) -> bool:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < MIN_FEATURE_HISTORY or len(vols) < MIN_FEATURE_HISTORY:
        return False

    avg_price = sum(prices) / len(prices)
    if avg_price <= 0:
        return False

    price_range_pct = (max(prices) - min(prices)) / avg_price
    drift_pct = (prices[-1] - prices[0]) / prices[0] if prices[0] > 0 else 0.0

    avg_old_vol = sum(vols[:-3]) / max(1, len(vols[:-3])) if len(vols) > 3 else 0.0
    recent_avg_vol = sum(vols[-3:]) / 3 if len(vols) >= 3 else 0.0

    if price_range_pct > MAX_ACCUM_RANGE:
        return False

    if abs(drift_pct) > MAX_ACCUM_DRIFT:
        return False

    if avg_old_vol > 0 and recent_avg_vol < avg_old_vol * MIN_VOLUME_IMPROVEMENT:
        return False

    return True

def is_breakout(product: str) -> bool:
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < MIN_FEATURE_HISTORY or len(vols) < MIN_FEATURE_HISTORY:
        return False

    last_price = prices[-1]
    prior_high = max(prices[:-1])

    if last_price <= prior_high * (1.0 + BREAKOUT_ADD_ON_PCT):
        return False

    avg_prior_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    if avg_prior_vol <= 0:
        return False

    if vols[-1] < avg_prior_vol * BREAKOUT_VOLUME_MULT:
        return False

    return True

def cooldown_active(product: str) -> bool:
    ts = last_exit_times.get(product, 0)
    if ts <= 0:
        return False
    return (time.time() - ts) < REENTRY_COOLDOWN_SECONDS

# ================= PAPER / LIVE EXECUTION =================

def paper_cash_available() -> float:
    return balance

def execution_cash_available() -> float:
    if live_mode_ready():
        return get_live_available_cash_usd()
    return paper_cash_available()

def execute_buy(product: str, intended_price: float, usd_size: float) -> Tuple[bool, dict]:
    if live_mode_ready():
        if LIVE_TRADING_REQUIRE_CONFIRM:
            available_cash = get_live_available_cash_usd()
            if available_cash < (usd_size + MIN_CASH_BUFFER):
                return False, {
                    "mode": "live",
                    "error": f"Insufficient live USD. Available=${available_cash:.2f}, needed>${usd_size + MIN_CASH_BUFFER:.2f}"
                }

        try:
            order_response = create_market_buy_order(product, usd_size)
            order_id = extract_order_id(order_response)
            if not order_id:
                return False, {
                    "mode": "live",
                    "error": "No order_id returned from buy order",
                    "raw": order_response
                }

            filled_payload = wait_for_order_fill(order_id)
            order = filled_payload.get("order", {})
            status = str(order.get("status", "")).upper()

            if status not in {"FILLED", "COMPLETED"}:
                return False, {
                    "mode": "live",
                    "error": f"Buy order not filled. Status={status}",
                    "order_id": order_id,
                    "raw": filled_payload
                }

            fill_price, base_size = parse_filled_buy(filled_payload, intended_price, usd_size)

            return True, {
                "mode": "live",
                "entry_price": fill_price,
                "usd_size": usd_size,
                "base_size": base_size,
                "order_id": order_id,
                "raw": filled_payload
            }

        except Exception as e:
            return False, {
                "mode": "live",
                "error": f"Live buy failed: {e}"
            }

    estimated_base = usd_size / intended_price if intended_price > 0 else 0.0
    return True, {
        "mode": "paper",
        "entry_price": intended_price,
        "usd_size": usd_size,
        "base_size": estimated_base,
        "order_id": "",
        "raw": {}
    }

def execute_sell(product: str, intended_price: float, base_size: float, usd_size: float) -> Tuple[bool, dict]:
    if live_mode_ready():
        try:
            available_base = get_live_available_base(product)
            sell_base = min(base_size, available_base)
            sell_base = round_down(sell_base, 8)

            if sell_base <= 0:
                return False, {
                    "mode": "live",
                    "error": f"No available {get_product_base_currency(product)} balance to sell"
                }

            order_response = create_market_sell_order(product, sell_base)
            order_id = extract_order_id(order_response)
            if not order_id:
                return False, {
                    "mode": "live",
                    "error": "No order_id returned from sell order",
                    "raw": order_response
                }

            filled_payload = wait_for_order_fill(order_id)
            order = filled_payload.get("order", {})
            status = str(order.get("status", "")).upper()

            if status not in {"FILLED", "COMPLETED"}:
                return False, {
                    "mode": "live",
                    "error": f"Sell order not filled. Status={status}",
                    "order_id": order_id,
                    "raw": filled_payload
                }

            exit_price, sold_base_size = parse_filled_sell(filled_payload, intended_price, sell_base)
            usd_value = sold_base_size * exit_price

            return True, {
                "mode": "live",
                "exit_price": exit_price,
                "sold_base_size": sold_base_size,
                "usd_value": usd_value,
                "order_id": order_id,
                "raw": filled_payload
            }

        except Exception as e:
            return False, {
                "mode": "live",
                "error": f"Live sell failed: {e}"
            }

    usd_value = usd_size * (intended_price / positions.get(product, {}).get("entry", intended_price)) if intended_price > 0 else usd_size
    return True, {
        "mode": "paper",
        "exit_price": intended_price,
        "sold_base_size": base_size,
        "usd_value": usd_value,
        "order_id": "",
        "raw": {}
    }

# ================= TRADING =================

def open_trade(product: str, price: float, features: Dict[str, float]) -> None:
    global balance

    if product in positions:
        return

    if len(positions) >= MAX_OPEN_TRADES:
        return

    if not live_mode_ready() and balance < TRADE_SIZE:
        return

    if live_mode_ready():
        available_cash = execution_cash_available()
        if available_cash < (TRADE_SIZE + MIN_CASH_BUFFER):
            return

    if cooldown_active(product):
        return

    if near_high_filter(product):
        return

    if not pullback_entry_ok(product):
        return

    current_ml_active = ml_is_active()
    score = ml_score(features)

    if current_ml_active and len(ml_data) >= ML_MIN_SAMPLES and score < ML_MIN_SCORE:
        return

    success, result = execute_buy(product, price, TRADE_SIZE)
    if not success:
        send(
            f"⚠️ BUY FAILED {product}\n"
            f"Mode: {'LIVE' if RUN_LIVE_TRADING else 'PAPER'}\n"
            f"Reason: {result.get('error', 'Unknown error')}"
        )
        return

    entry_price = float(result["entry_price"])
    usd_size = float(result["usd_size"])
    base_size = float(result["base_size"])
    order_id = str(result.get("order_id", ""))
    mode = str(result.get("mode", "paper"))

    if mode == "paper":
        balance -= usd_size

    positions[product] = {
        "entry": entry_price,
        "size": usd_size,
        "base_size": base_size,
        "peak": entry_price,
        "features": features,
        "added_on_breakout": False,
        "opened_at": int(time.time()),
        "ml_score": round(score, 4),
        "ml_active_at_entry": current_ml_active,
        "trail_armed": False,
        "trail_stop_price": 0.0,
        "live_order_id": order_id,
        "last_buy_fill_price": entry_price,
        "mode": mode
    }
    save_state()

    prefix = "🟢 LIVE ENTRY" if mode == "live" else "🟡 PAPER ENTRY"
    ml_text = format_ml_display(score, current_ml_active)

    send(
        f"🤖 MACHINE LEARNING {get_ml_status_text()}\n"
        f"{prefix} {product}\n"
        f"Price: {entry_price:.6f}\n"
        f"ML Score: {ml_text}\n"
        f"USD Size: ${usd_size:.2f}\n"
        f"Coin Size: {base_size:.8f}\n"
        f"{'Paper Balance' if mode == 'paper' else 'Live USD Check Complete'}: "
        f"{f'${balance:.2f}' if mode == 'paper' else 'OK'}"
    )

def add_trade(product: str, price: float) -> None:
    global balance

    if product not in positions:
        return

    pos = positions[product]
    if pos.get("added_on_breakout"):
        return

    if not live_mode_ready() and balance < TRADE_SIZE:
        return

    if live_mode_ready():
        available_cash = execution_cash_available()
        if available_cash < (TRADE_SIZE + MIN_CASH_BUFFER):
            return

    entry = float(pos["entry"])
    current_gain = (price - entry) / entry if entry > 0 else 0.0
    if current_gain < MIN_ADD_ON_GAIN:
        return

    success, result = execute_buy(product, price, TRADE_SIZE)
    if not success:
        send(
            f"⚠️ ADD-ON BUY FAILED {product}\n"
            f"Mode: {'LIVE' if RUN_LIVE_TRADING else 'PAPER'}\n"
            f"Reason: {result.get('error', 'Unknown error')}"
        )
        return

    add_price = float(result["entry_price"])
    add_usd = float(result["usd_size"])
    add_base = float(result["base_size"])
    mode = str(result.get("mode", "paper"))

    old_usd = float(pos["size"])
    old_base = float(pos.get("base_size", 0.0))

    total_usd = old_usd + add_usd
    total_base = old_base + add_base

    if mode == "paper":
        balance -= add_usd

    weighted_entry = entry
    if total_base > 0:
        weighted_entry = ((entry * old_base) + (add_price * add_base)) / total_base

    pos["entry"] = weighted_entry
    pos["size"] = total_usd
    pos["base_size"] = total_base
    pos["peak"] = max(float(pos.get("peak", add_price)), add_price)
    pos["added_on_breakout"] = True
    pos["last_buy_fill_price"] = add_price
    pos["mode"] = mode
    save_state()

    send(
        f"🤖 MACHINE LEARNING {get_ml_status_text()}\n"
        f"{'🚀 LIVE ADD ON BREAKOUT' if mode == 'live' else '🚀 PAPER ADD ON BREAKOUT'} {product}\n"
        f"Add Price: {add_price:.6f}\n"
        f"New Avg Entry: {weighted_entry:.6f}\n"
        f"New USD Size: ${total_usd:.2f}\n"
        f"New Coin Size: {total_base:.8f}\n"
        f"Balance: ${balance:.2f}"
    )

def close_trade(product: str, price: float, reason: str) -> None:
    global balance

    pos = positions.get(product)
    if not pos:
        return

    entry = float(pos["entry"])
    usd_size = float(pos["size"])
    base_size = float(pos.get("base_size", 0.0))
    features = pos.get("features", {})
    score = float(pos.get("ml_score", 0.0))
    ml_was_active = bool(pos.get("ml_active_at_entry", False))
    mode = str(pos.get("mode", "paper"))

    success, result = execute_sell(product, price, base_size, usd_size)
    if not success:
        send(
            f"⚠️ EXIT FAILED {product} ({reason})\n"
            f"Mode: {'LIVE' if RUN_LIVE_TRADING else 'PAPER'}\n"
            f"Reason: {result.get('error', 'Unknown error')}"
        )
        return

    positions.pop(product, None)

    exit_price = float(result["exit_price"])
    sold_base_size = float(result["sold_base_size"])
    usd_value = float(result["usd_value"])

    if mode == "paper":
        pnl_pct = (exit_price - entry) / entry if entry > 0 else 0.0
        profit = usd_size * pnl_pct
        balance += usd_size + profit
    else:
        profit = usd_value - usd_size
        pnl_pct = (profit / usd_size) if usd_size > 0 else 0.0

    last_exit_times[product] = int(time.time())
    save_state()

    log_trade(features, pnl_pct, product, entry, exit_price, reason, usd_size, profit, score, ml_was_active)

    stats = get_account_stats()
    ml_text = format_ml_display(score, ml_was_active)

    prefix = "🔴 LIVE EXIT" if mode == "live" else "🔴 PAPER EXIT"
    send(
        f"🤖 MACHINE LEARNING {get_ml_status_text()}\n"
        f"{prefix} {product} ({reason})\n"
        f"Entry: {entry:.6f}\n"
        f"Exit: {exit_price:.6f}\n"
        f"Coin Size Sold: {sold_base_size:.8f}\n"
        f"ML Score: {ml_text}\n"
        f"PnL: ${profit:.2f} ({pnl_pct * 100:.2f}%)\n"
        f"Cash Balance: ${balance:.2f}\n"
        f"Realized PnL: ${stats['realized_pnl']:.2f}\n"
        f"Wins/Losses: {int(stats['wins'])}/{int(stats['losses'])}\n"
        f"Win Rate: {stats['win_rate']:.1f}%"
    )

def manage_position(product: str, price: float) -> None:
    if product not in positions:
        return

    pos = positions[product]
    entry = float(pos["entry"])
    peak = max(float(pos.get("peak", price)), price)
    pos["peak"] = peak

    change = (price - entry) / entry if entry > 0 else 0.0

    if change >= TAKE_PROFIT:
        close_trade(product, price, "TP")
        return

    if change <= -STOP_LOSS:
        close_trade(product, price, "SL")
        return

    if not pos.get("trail_armed", False) and change >= TRAILING_ARM:
        pos["trail_armed"] = True
        pos["trail_stop_price"] = peak * (1.0 - TRAILING_STOP)
        save_state()

        send(
            f"🤖 MACHINE LEARNING {get_ml_status_text()}\n"
            f"🟦 TRAILING ARMED {product}\n"
            f"Entry: {entry:.6f}\n"
            f"Peak: {peak:.6f}\n"
            f"Trail Stop: {float(pos['trail_stop_price']):.6f}"
        )

    if pos.get("trail_armed", False):
        new_trail = peak * (1.0 - TRAILING_STOP)
        if new_trail > float(pos.get("trail_stop_price", 0.0)):
            pos["trail_stop_price"] = new_trail
            save_state()

        if price <= float(pos.get("trail_stop_price", 0.0)):
            close_trade(product, price, "TRAIL")
            return

    save_state()

# ================= STATUS =================

def send_update() -> None:
    stats = get_account_stats()
    mode_label = "LIVE TRADING ACTIVE" if RUN_LIVE_TRADING else "LIVE READY / PAPER ACTIVE"

    lines = [
        f"🤖 MACHINE LEARNING {get_ml_status_text()}",
        "📊 3-MIN UPDATE",
        f"Mode: {mode_label}",
        f"Starting Balance: ${START_BALANCE:.2f}",
        f"Cash Balance: ${stats['cash_balance']:.2f}",
        f"Open Trades: {len(positions)}",
        f"ML Samples: {len(ml_data)}",
        "",
        f"Realized PnL: ${stats['realized_pnl']:.2f}",
        f"Unrealized PnL: ${stats['open_pnl']:.2f}",
        f"Total PnL: ${stats['total_pnl']:.2f}",
        f"Open Position Value: ${stats['open_value']:.2f}",
        f"Total Account Value: ${stats['total_account_value']:.2f}",
        "",
        f"Closed Trades: {int(stats['total_closed'])}",
        f"Wins: {int(stats['wins'])}",
        f"Losses: {int(stats['losses'])}",
        f"Win Rate: {stats['win_rate']:.1f}%",
        f"Avg Win: ${stats['avg_win']:.2f}",
        f"Avg Loss: ${stats['avg_loss']:.2f}",
        f"Best Trade: ${stats['best_trade']:.2f}",
        f"Worst Trade: ${stats['worst_trade']:.2f}",
        ""
    ]

    if positions:
        for product, pos in positions.items():
            prices = list(price_history[product])
            current_price = prices[-1] if prices else float(pos["entry"])
            entry = float(pos["entry"])
            size = float(pos["size"])
            base_size = float(pos.get("base_size", 0.0))
            value = base_size * current_price if base_size > 0 else size * (current_price / entry if entry > 0 else 1.0)
            pnl = value - size
            pnl_pct = ((current_price - entry) / entry * 100) if entry > 0 else 0.0

            trail_text = ""
            if pos.get("trail_armed", False):
                trail_text = f" | Trail {float(pos.get('trail_stop_price', 0.0)):.6f}"

            ml_text = format_ml_display(
                float(pos.get("ml_score", 0.0)),
                bool(pos.get("ml_active_at_entry", False))
            )

            lines.append(
                f"{product} | Entry {entry:.6f} | Now {current_price:.6f} | "
                f"PnL ${pnl:.2f} ({pnl_pct:.2f}%) | "
                f"USD ${size:.2f} | Coins {base_size:.8f} | "
                f"ML {ml_text}{trail_text}"
            )
    else:
        lines.append("No open positions.")

    if RUN_LIVE_TRADING:
        try:
            live_cash = get_live_available_cash_usd()
            lines.extend([
                "",
                f"Live USD Available: ${live_cash:.2f}"
            ])
        except Exception:
            lines.extend([
                "",
                "Live USD Available: unavailable"
            ])

    send("\n".join(lines))

# ================= STARTUP CHECKS =================

def startup_checks() -> None:
    send(
        f"🤖 MACHINE LEARNING {get_ml_status_text()}\n"
        "🚀 SCANNER STARTED\n"
        f"Mode: {'LIVE' if RUN_LIVE_TRADING else 'PAPER'}"
    )

    if RUN_LIVE_TRADING:
        if not COINBASE_API_KEY or not COINBASE_API_PRIVATE_KEY:
            send("⚠️ RUN_LIVE_TRADING=true but Coinbase credentials are missing.")
        else:
            try:
                live_cash = get_live_available_cash_usd()
                send(f"✅ Coinbase auth check passed\nLive USD Available: ${live_cash:.2f}")
            except Exception as e:
                send(f"⚠️ Coinbase auth check failed\n{e}")

# ================= MAIN =================

load_state()
startup_checks()

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
        print in(f"Main loop error: {e}")
        time.sleep(5)
