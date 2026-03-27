import os
import time
import requests
from datetime import datetime, UTC

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "50000"))
MIN_VOLUME_5M = float(os.getenv("MIN_VOLUME_5M", "10000"))
BUY_SELL_RATIO_THRESHOLD = float(os.getenv("BUY_SELL_RATIO_THRESHOLD", "2.0"))
BOTTOM_RANGE_PCT = float(os.getenv("BOTTOM_RANGE_PCT", "0.08"))

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; BaseScanner/1.0)"
    }
)

# DexScreener search endpoint works; pair listing endpoint you used did not.
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search/?q=base"

seen_tokens = set()

# ============================================================
# HELPERS
# ============================================================

def now_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str) -> None:
    print(f"[{now_str()}] {msg}")


def safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def send_telegram(msg: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("Telegram not configured; skipping alert.")
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = SESSION.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": msg,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not resp.ok:
            log(f"Telegram error: {resp.text}")
    except Exception as e:
        log(f"Telegram exception: {e}")


# ============================================================
# MACD
# ============================================================

def ema(values, period):
    if not values:
        return []

    k = 2 / (period + 1)
    ema_vals = [values[0]]

    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))

    return ema_vals


def calculate_macd(prices):
    if len(prices) < MACD_SLOW:
        return None, None, None

    ema_fast = ema(prices, MACD_FAST)
    ema_slow = ema(prices, MACD_SLOW)

    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, MACD_SIGNAL)
    hist = [m - s for m, s in zip(macd_line, signal_line)]

    return macd_line, signal_line, hist


# ============================================================
# BASE SCANNER
# ============================================================

def get_base_pairs():
    """
    Search DexScreener for Base-related pairs, then keep only actual Base chain pairs.
    """
    try:
        r = SESSION.get(DEXSCREENER_SEARCH_URL, timeout=25)
        r.raise_for_status()
        data = r.json()
        pairs = data.get("pairs", [])

        base_pairs = []
        for p in pairs:
            chain_id = (p.get("chainId") or "").lower()
            if chain_id == "base":
                base_pairs.append(p)

        return base_pairs

    except Exception as e:
        log(f"DexScreener fetch error: {e}")
        return []


def build_synthetic_prices(current_price: float, h24_change_pct: float):
    """
    DexScreener search results do not provide candle history.
    This builds a lightweight synthetic curve so MACD can still be approximated.
    """
    current_price = max(current_price, 1e-12)
    h24_change_pct = safe_float(h24_change_pct, 0.0)

    denom = 1 + (h24_change_pct / 100)
    if denom == 0:
        start_price = current_price
    else:
        start_price = current_price / denom

    prices = []
    for i in range(30):
        t = i / 29 if 29 else 1
        px = start_price + (current_price - start_price) * t
        prices.append(max(px, 1e-12))

    return prices


def analyze_pair(pair):
    try:
        chain_id = (pair.get("chainId") or "").lower()
        if chain_id != "base":
            return None

        dex_id = pair.get("dexId", "")
        pair_url = pair.get("url", "")

        base_token = pair.get("baseToken", {}) or {}
        quote_token = pair.get("quoteToken", {}) or {}

        symbol = base_token.get("symbol", "UNKNOWN")
        name = base_token.get("name", symbol)
        pair_address = pair.get("pairAddress", "")

        liquidity = safe_float((pair.get("liquidity", {}) or {}).get("usd", 0))
        volume_5m = safe_float((pair.get("volume", {}) or {}).get("m5", 0))
        price = safe_float(pair.get("priceUsd", 0))
        h24_change = safe_float((pair.get("priceChange", {}) or {}).get("h24", 0))

        txns_m5 = ((pair.get("txns", {}) or {}).get("m5", {}) or {})
        buys_5m = int(txns_m5.get("buys", 0) or 0)
        sells_5m = int(txns_m5.get("sells", 0) or 0)

        buy_sell_ratio = buys_5m / max(sells_5m, 1)

        if liquidity < MIN_LIQUIDITY:
            return None

        if volume_5m < MIN_VOLUME_5M:
            return None

        if buy_sell_ratio < BUY_SELL_RATIO_THRESHOLD:
            return None

        if price <= 0:
            return None

        # Approximate "near bottom" using current price relative to implied prior move
        approx_24h_low = price / (1 + abs(h24_change / 100)) if (1 + abs(h24_change / 100)) > 0 else price
        distance_from_low = abs(price - approx_24h_low) / max(approx_24h_low, 1e-12)

        if distance_from_low > BOTTOM_RANGE_PCT:
            return None

        prices = build_synthetic_prices(price, h24_change)
        macd_line, signal_line, hist = calculate_macd(prices)

        if not macd_line or not signal_line or not hist:
            return None

        if len(macd_line) < 3 or len(signal_line) < 3 or len(hist) < 3:
            return None

        # Pre-crossover / aggressive curl logic
        macd_curl_up = (
            macd_line[-1] < signal_line[-1]
            and macd_line[-1] > macd_line[-2] > macd_line[-3]
            and hist[-1] > hist[-2] > hist[-3]
        )

        # Early crossover confirmation logic
        crossover_now = (
            macd_line[-2] <= signal_line[-2]
            and macd_line[-1] > signal_line[-1]
            and hist[-1] > hist[-2]
        )

        if not macd_curl_up and not crossover_now:
            return None

        signal_type = "MACD PRE-CROSS CURL" if macd_curl_up and not crossover_now else "MACD BULLISH CROSS"

        return {
            "symbol": symbol,
            "name": name,
            "price": price,
            "liquidity": liquidity,
            "volume_5m": volume_5m,
            "buys_5m": buys_5m,
            "sells_5m": sells_5m,
            "buy_sell_ratio": buy_sell_ratio,
            "distance_from_low": distance_from_low,
            "pair_url": pair_url,
            "pair_address": pair_address,
            "quote_symbol": quote_token.get("symbol", ""),
            "dex_id": dex_id,
            "signal_type": signal_type,
        }

    except Exception as e:
        log(f"Pair analysis error: {e}")
        return None


def scan_base():
    pairs = get_base_pairs()

    if not pairs:
        log("No Base pairs returned.")
        return

    alerts_sent = 0

    for pair in pairs:
        result = analyze_pair(pair)
        if not result:
            continue

        key = result["pair_address"] or result["symbol"]
        if key in seen_tokens:
            continue

        seen_tokens.add(key)
        alerts_sent += 1

        msg = (
            f"🚀 BASE BUYING + MACD ALERT\n\n"
            f"Signal: {result['signal_type']}\n"
            f"Token: {result['name']} ({result['symbol']})\n"
            f"Price: ${result['price']:.8f}\n"
            f"Liquidity: ${result['liquidity']:.0f}\n"
            f"5m Volume: ${result['volume_5m']:.0f}\n"
            f"5m Buys/Sells: {result['buys_5m']}/{result['sells_5m']}\n"
            f"Buy/Sell Ratio: {result['buy_sell_ratio']:.2f}\n"
            f"Distance From Bottom: {result['distance_from_low'] * 100:.2f}%\n"
            f"DEX: {result['dex_id']}\n\n"
            f"Chart:\n{result['pair_url']}"
        )

        send_telegram(msg)

    log(f"Base scan complete. Alerts sent: {alerts_sent}")


# ============================================================
# MAIN
# ============================================================

def startup_banner():
    log("BOT STARTED")
    log(f"SCAN_INTERVAL={SCAN_INTERVAL}")
    log(f"MIN_LIQUIDITY={MIN_LIQUIDITY}")
    log(f"MIN_VOLUME_5M={MIN_VOLUME_5M}")
    log(f"BUY_SELL_RATIO_THRESHOLD={BUY_SELL_RATIO_THRESHOLD}")
    log(f"BOTTOM_RANGE_PCT={BOTTOM_RANGE_PCT}")


def main():
    startup_banner()

    while True:
        try:
            scan_base()
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log("Bot stopped by user.")
            break
        except Exception as e:
            log(f"Main loop error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
