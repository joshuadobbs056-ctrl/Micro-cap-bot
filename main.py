import os
import time
import json
import requests
from datetime import datetime, timezone
from collections import deque

# ================= CONFIG =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
UPDATE_INTERVAL = 180  # FORCE 3 MINUTES

START_BALANCE = float(os.getenv("START_BALANCE", "500"))
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "50"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "5"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.05"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "0.03"))

ENABLE_ADD_ON_BREAKOUT = True

ML_FILE = "ml_data.json"

PRODUCTS = [
    "BTC-USD","ETH-USD","SOL-USD","AVAX-USD","LINK-USD","MATIC-USD",
    "DOGE-USD","ADA-USD","XRP-USD","LTC-USD","BCH-USD","ATOM-USD",
    "APT-USD","ARB-USD","OP-USD","INJ-USD","NEAR-USD","FIL-USD",
    "SUI-USD","SEI-USD","PEPE-USD","BONK-USD","WIF-USD"
]

SESSION = requests.Session()

# ================= STATE =================

balance = START_BALANCE
positions = {}
price_history = {p: deque(maxlen=10) for p in PRODUCTS}
volume_history = {p: deque(maxlen=10) for p in PRODUCTS}

last_update = time.time()

# ================= ML =================

def load_ml():
    if not os.path.exists(ML_FILE):
        return []
    with open(ML_FILE, "r") as f:
        return json.load(f)

def save_ml(data):
    with open(ML_FILE, "w") as f:
        json.dump(data, f)

ml_data = load_ml()

def ml_score(features):
    if not ml_data:
        return 0.5

    scores = []
    for trade in ml_data:
        similarity = 0
        for k in features:
            similarity += 1 - abs(features[k] - trade["features"][k])
        similarity /= len(features)

        scores.append(similarity * trade["result"])

    return sum(scores) / len(scores) if scores else 0.5

def log_trade(features, result):
    ml_data.append({
        "features": features,
        "result": result
    })
    save_ml(ml_data)

# ================= UTIL =================

def now():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(msg)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": CHAT_ID,
            "text": msg
        })
    except:
        pass

def get_candle(product):
    url = f"https://api.coinbase.com/api/v3/brokerage/market/products/{product}/candles"
    params = {"granularity": "FIVE_MINUTE", "limit": 5}
    r = SESSION.get(url, params=params)
    if r.status_code != 200:
        return None
    data = r.json().get("candles", [])
    if not data:
        return None
    latest = data[-1]
    return float(latest["close"]), float(latest["volume"])

# ================= SIGNAL =================

def extract_features(product):
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 5:
        return None

    volatility = (max(prices) - min(prices)) / prices[-1]
    vol_trend = vols[-1] / (sum(vols[:-1]) / len(vols[:-1]) + 1e-9)

    return {
        "volatility": min(volatility, 1),
        "vol_trend": min(vol_trend / 3, 1)
    }

def is_accumulation(product):
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 5:
        return False

    price_range = max(prices) - min(prices)
    avg_price = sum(prices) / len(prices)

    if price_range / avg_price > 0.02:
        return False

    if not all(vols[i] <= vols[i+1] for i in range(len(vols)-1)):
        return False

    return True

def is_breakout(product):
    prices = list(price_history[product])
    vols = list(volume_history[product])

    if len(prices) < 5:
        return False

    if prices[-1] <= max(prices[:-1]):
        return False

    avg_vol = sum(vols[:-1]) / len(vols[:-1])
    return vols[-1] > avg_vol * 1.5

# ================= TRADING =================

def open_trade(product, price, features):
    global balance

    score = ml_score(features)

    if score < 0.55:
        return  # ML FILTER

    if len(positions) >= MAX_OPEN_TRADES:
        return

    if balance < TRADE_SIZE:
        return

    balance -= TRADE_SIZE

    positions[product] = {
        "entry": price,
        "size": TRADE_SIZE,
        "peak": price,
        "features": features
    }

    send(f"🟡 ENTRY {product}\nPrice: {price:.4f}\nML Score: {score:.2f}")

def add_trade(product, price):
    global balance
    if product not in positions or balance < TRADE_SIZE:
        return

    balance -= TRADE_SIZE
    positions[product]["size"] += TRADE_SIZE

    send(f"🚀 ADD {product} @ {price:.4f}")

def close_trade(product, price, reason):
    global balance

    pos = positions.pop(product)
    entry = pos["entry"]
    size = pos["size"]

    pnl_pct = (price - entry) / entry
    profit = size * pnl_pct

    balance += size + profit

    result = 1 if pnl_pct > 0 else -1
    log_trade(pos["features"], result)

    send(f"🔴 EXIT {product} ({reason})\nPnL: {profit:.2f} ({pnl_pct*100:.2f}%)")

# ================= STATUS =================

def send_update():
    msg = f"📊 UPDATE\nBalance: ${balance:.2f}\nOpen Trades: {len(positions)}\n\n"
    for p, pos in positions.items():
        msg += f"{p} | Entry {pos['entry']:.2f} | Size ${pos['size']}\n"
    send(msg)

# ================= MAIN =================

send("🚀 ML ACCUMULATION SCANNER STARTED")

while True:
    try:
        for product in PRODUCTS:
            data = get_candle(product)
            if not data:
                continue

            price, volume = data
            price_history[product].append(price)
            volume_history[product].append(volume)

            features = extract_features(product)
            if not features:
                continue

            if product not in positions and is_accumulation(product):
                open_trade(product, price, features)

            if ENABLE_ADD_ON_BREAKOUT and product in positions and is_breakout(product):
                add_trade(product, price)

            if product in positions:
                pos = positions[product]
                entry = pos["entry"]

                change = (price - entry) / entry

                if change >= TAKE_PROFIT:
                    close_trade(product, price, "TP")
                    continue

                if change <= -STOP_LOSS:
                    close_trade(product, price, "SL")
                    continue

        # --- FORCED 3 MINUTE UPDATE ---
        if time.time() - last_update >= UPDATE_INTERVAL:
            send_update()
            last_update = time.time()

        time.sleep(SCAN_INTERVAL)

    except Exception as e:
        print("ERROR:", e)
        time.sleep(5)
