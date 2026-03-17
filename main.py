import os
import time
import math
import requests
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

# ============================================================
# COINBASE FUTURES STRONG-MOVE ALERT BOT
# ============================================================
# What it does:
# - Pulls recent candles from Coinbase public market-data endpoints
# - Scores for strong UP / DOWN movement setups
# - Sends Telegram alerts
# - Uses BIG GREEN / BIG RED arrows in every alert
#
# Coinbase docs:
# - Public Advanced Trade market data exists under /market/products/*
# - Product candles require start, end, granularity, and support
#   ONE_MINUTE / FIVE_MINUTE / FIFTEEN_MINUTE / etc.
# - Coinbase product APIs distinguish FUTURE products
# - Coinbase docs show perpetual futures IDs like BTC-PERP-INTX
#
# ENV EXAMPLES:
# TELEGRAM_TOKEN=123456:ABCDEF
# CHAT_ID=123456789
# SCAN_INTERVAL=60
# FUTURES_PRODUCTS=BTC-PERP-INTX,ETH-PERP-INTX
# GRANULARITY=ONE_MINUTE
# CANDLE_LIMIT=120
# RSI_PERIOD=14
# MOMENTUM_BARS=5
# VOLUME_SPIKE_MULT=2.0
# RANGE_SPIKE_MULT=1.8
# ALERT_SCORE_THRESHOLD=3
# ALERT_COOLDOWN_MINUTES=20
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
CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "120"))

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
MOMENTUM_BARS = int(os.getenv("MOMENTUM_BARS", "5"))
VOLUME_SPIKE_MULT = float(os.getenv("VOLUME_SPIKE_MULT", "2.0"))
RANGE_SPIKE_MULT = float(os.getenv("RANGE_SPIKE_MULT", "1.8"))
ALERT_SCORE_THRESHOLD = int(os.getenv("ALERT_SCORE_THRESHOLD", "3"))
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "20"))

DEBUG = os.getenv("DEBUG", "off").strip().lower() == "on"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Coinbase-Futures-StrongMove-Bot/1.0",
    "Accept": "application/json",
    "Cache-Control": "no-cache",
})


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


def send_telegram(msg: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("Telegram not configured. Printing alert instead:")
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


# ------------------------------------------------------------
# COINBASE DATA
# ------------------------------------------------------------
def get_candles(product_id: str,
                granularity: str = GRANULARITY,
                limit: int = CANDLE_LIMIT) -> Optional[List[Dict]]:
    """
    Public endpoint:
      GET /api/v3/brokerage/market/products/{product_id}/candles
    Requires:
      start, end, granularity
    """
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

        # Sort oldest -> newest
        candles.sort(key=lambda x: int(x["start"]))
        return candles

    except Exception as e:
        log(f"get_candles error for {product_id}: {e}")
        return None


# ------------------------------------------------------------
# INDICATORS
# ------------------------------------------------------------
def extract_ohlcv(candles: List[Dict]) -> Tuple[List[float], List[float], List[float], List[float], List[int]]:
    closes, highs, lows, opens, volumes = [], [], [], [], []
    for c in candles:
        opens.append(safe_float(c.get("open")))
        highs.append(safe_float(c.get("high")))
        lows.append(safe_float(c.get("low")))
        closes.append(safe_float(c.get("close")))
        volumes.append(int(float(c.get("volume", 0) or 0)))
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

    avg_vol = sma([float(v) for v in volumes[:-1]], 20) if len(volumes) > 20 else sma([float(v) for v in volumes], len(volumes))
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

    # Volume expansion
    if vol_multiple >= VOLUME_SPIKE_MULT:
        bullish_score += 1
        bearish_score += 1
        reasons_up.append(f"volume x{vol_multiple:.2f}")
        reasons_down.append(f"volume x{vol_multiple:.2f}")

    # Candle range expansion
    if range_multiple >= RANGE_SPIKE_MULT:
        bullish_score += 1
        bearish_score += 1
        reasons_up.append(f"range x{range_multiple:.2f}")
        reasons_down.append(f"range x{range_multiple:.2f}")

    # Momentum direction
    if momentum_pct >= 0.35:
        bullish_score += 1
        reasons_up.append(f"momentum {momentum_pct:+.2f}%")
    elif momentum_pct <= -0.35:
        bearish_score += 1
        reasons_down.append(f"momentum {momentum_pct:+.2f}%")

    # RSI
    if rsi >= 63:
        bullish_score += 1
        reasons_up.append(f"RSI {rsi:.1f}")
    elif rsi <= 37:
        bearish_score += 1
        reasons_down.append(f"RSI {rsi:.1f}")

    # Breakout logic
    if breakout_up:
        bullish_score += 1
        reasons_up.append("20-candle breakout")
    if breakout_down:
        bearish_score += 1
        reasons_down.append("20-candle breakdown")

    # Candle body conviction
    if body_pct >= 0.20:
        if last_close > last_open:
            bullish_score += 1
            reasons_up.append(f"strong body {body_pct:.2f}%")
        elif last_close < last_open:
            bearish_score += 1
            reasons_down.append(f"strong body {body_pct:.2f}%")

    direction = None
    score = 0
    reasons = []

    if bullish_score >= ALERT_SCORE_THRESHOLD and bullish_score > bearish_score:
        direction = "UP"
        score = bullish_score
        reasons = reasons_up
    elif bearish_score >= ALERT_SCORE_THRESHOLD and bearish_score > bullish_score:
        direction = "DOWN"
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
        "score": score,
        "price": last_close,
        "rsi": rsi,
        "momentum_pct": momentum_pct,
        "vol_multiple": vol_multiple,
        "range_multiple": range_multiple,
        "reasons": reasons[:5],
        "timestamp": utc_now_ts(),
    }


# ------------------------------------------------------------
# ALERT FORMATTING
# ------------------------------------------------------------
def build_alert(signal: Dict) -> str:
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


# ------------------------------------------------------------
# COOLDOWN / DEDUPE
# ------------------------------------------------------------
LAST_ALERT_AT: Dict[str, int] = {}


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
# MAIN LOOP
# ------------------------------------------------------------
def startup_message() -> str:
    return (
        "🤖 Coinbase Futures Strong-Move Bot Started\n\n"
        f"Products: {', '.join(FUTURES_PRODUCTS)}\n"
        f"Granularity: {GRANULARITY}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n"
        f"Score Threshold: {ALERT_SCORE_THRESHOLD}\n"
        f"Cooldown: {ALERT_COOLDOWN_MINUTES}m"
    )


def main() -> None:
    log("Starting Coinbase futures strong-move bot...")
    send_telegram(startup_message())

    while True:
        try:
            for product_id in FUTURES_PRODUCTS:
                signal = analyze_product(product_id)
                if not signal:
                    continue

                if should_alert(signal["product_id"], signal["direction"]):
                    msg = build_alert(signal)
                    send_telegram(msg)
                    log(f"ALERT SENT: {signal['product_id']} {signal['direction']} score={signal['score']}")
                else:
                    if DEBUG:
                        log(f"Cooldown active: {signal['product_id']} {signal['direction']}")

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log("Stopped by user.")
            break
        except Exception as e:
            log(f"Main loop error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
