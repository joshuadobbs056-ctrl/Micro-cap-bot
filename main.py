import os
import re
import time
import requests
from datetime import datetime

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
LISTING_CHECK_INTERVAL = int(os.getenv("LISTING_CHECK_INTERVAL", "300"))

MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "50000"))
MIN_VOLUME_5M = float(os.getenv("MIN_VOLUME_5M", "10000"))
BUY_SELL_RATIO_THRESHOLD = float(os.getenv("BUY_SELL_RATIO_THRESHOLD", "2.0"))
BOTTOM_RANGE_PCT = float(os.getenv("BOTTOM_RANGE_PCT", "0.08"))

# Coinbase listing phase filters
ALERT_TRANSFER_ONLY = os.getenv("ALERT_TRANSFER_ONLY", "true").strip().lower() == "true"
ALERT_LIMIT_ONLY = os.getenv("ALERT_LIMIT_ONLY", "false").strip().lower() == "true"
ALERT_AUCTION = os.getenv("ALERT_AUCTION", "false").strip().lower() == "true"
ALERT_FULL_TRADING = os.getenv("ALERT_FULL_TRADING", "true").strip().lower() == "true"

# Optional: suppress repeat alerts for same asset+phase forever during runtime
# If you want repeats after restart only, keep as-is.
seen_tokens = set()
seen_listings = set()

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; BaseScanner/1.0; +https://coinbase.com)"
    }
)

DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs/base"
COINBASE_STATUS_URL = "https://status.exchange.coinbase.com/"
COINBASE_MARKETS_RSS_URL = "https://blog.coinbase.com/feed"
COINBASE_MARKETS_X_FALLBACK = "https://x.com/CoinbaseMarkets"

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ============================================================
# HELPERS
# ============================================================

def now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def log(msg: str) -> None:
    print(f"[{now_str()}] {msg}")


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
    try:
        r = SESSION.get(DEXSCREENER_PAIRS_URL, timeout=25)
        r.raise_for_status()
        data = r.json()
        return data.get("pairs", [])
    except Exception as e:
        log(f"DexScreener fetch error: {e}")
        return []


def build_synthetic_prices_from_change(current_price: float, h24_change_pct: float):
    """
    DexScreener pair endpoint does not provide a full candle series here.
    This creates a synthetic curve so the MACD section still works structurally.
    It is not true market MACD and should be treated as a lightweight proxy only.
    """
    current_price = max(current_price, 1e-12)
    h24_change_pct = safe_float(h24_change_pct, 0.0)

    start_price = current_price / (1 + (h24_change_pct / 100)) if (1 + (h24_change_pct / 100)) != 0 else current_price
    prices = []

    for i in range(30):
        t = i / 29 if 29 else 1
        px = start_price + (current_price - start_price) * t
        prices.append(max(px, 1e-12))

    return prices


def analyze_pair(pair):
    try:
        pair_url = pair.get("url", "")
        base_token = pair.get("baseToken", {}) or {}
        quote_token = pair.get("quoteToken", {}) or {}
        symbol = base_token.get("symbol", "UNKNOWN")
        name = base_token.get("name", symbol)

        liquidity = safe_float((pair.get("liquidity", {}) or {}).get("usd", 0))
        volume_5m = safe_float((pair.get("volume", {}) or {}).get("m5", 0))
        price = safe_float(pair.get("priceUsd", 0))
        h24_change = safe_float((pair.get("priceChange", {}) or {}).get("h24", 0))

        buys_5m = int(((pair.get("txns", {}) or {}).get("m5", {}) or {}).get("buys", 0) or 0)
        sells_5m = int(((pair.get("txns", {}) or {}).get("m5", {}) or {}).get("sells", 0) or 0)
        buy_sell_ratio = buys_5m / max(sells_5m, 1)

        if liquidity < MIN_LIQUIDITY:
            return None

        if volume_5m < MIN_VOLUME_5M:
            return None

        if buy_sell_ratio < BUY_SELL_RATIO_THRESHOLD:
            return None

        if price <= 0:
            return None

        # Approximate 24h low using current price and 24h change.
        # This is rough, but it keeps the "near bottom" logic in place.
        denom = 1 + abs(h24_change / 100)
        approx_24h_low = price / denom if denom > 0 else price
        distance_from_low = abs(price - approx_24h_low) / max(approx_24h_low, 1e-12)

        if distance_from_low > BOTTOM_RANGE_PCT:
            return None

        synthetic_prices = build_synthetic_prices_from_change(price, h24_change)
        macd_line, signal_line, hist = calculate_macd(synthetic_prices)
        if not macd_line or not signal_line or not hist or len(macd_line) < 3 or len(hist) < 3:
            return None

        macd_curling_up = (
            macd_line[-1] < signal_line[-1]
            and macd_line[-1] > macd_line[-2] > macd_line[-3]
            and hist[-1] > hist[-2] > hist[-3]
        )

        if not macd_curling_up:
            return None

        return {
            "symbol": symbol,
            "name": name,
            "price": price,
            "liquidity": liquidity,
            "volume_5m": volume_5m,
            "buy_sell_ratio": buy_sell_ratio,
            "buys_5m": buys_5m,
            "sells_5m": sells_5m,
            "quote_symbol": quote_token.get("symbol", ""),
            "pair_url": pair_url,
            "distance_from_low": distance_from_low,
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

        token_key = result["symbol"]
        if token_key in seen_tokens:
            continue

        seen_tokens.add(token_key)
        alerts_sent += 1

        msg = (
            f"🚀 BASE REVERSAL SETUP\n\n"
            f"Token: {result['name']} ({result['symbol']})\n"
            f"Price: ${result['price']:.8f}\n"
            f"Liquidity: ${result['liquidity']:.0f}\n"
            f"5m Volume: ${result['volume_5m']:.0f}\n"
            f"5m Buys/Sells: {result['buys_5m']}/{result['sells_5m']}\n"
            f"Buy/Sell Ratio: {result['buy_sell_ratio']:.2f}\n"
            f"Bottom Distance: {result['distance_from_low'] * 100:.2f}%\n"
            f"MACD: Curling up pre-crossover\n\n"
            f"Chart:\n{result['pair_url']}"
        )
        send_telegram(msg)

    log(f"Base scan complete. Alerts sent: {alerts_sent}")


# ============================================================
# COINBASE CONFIRMED LISTING WATCHER
# ============================================================

def get_enabled_phase_keywords():
    phase_keywords = []

    if ALERT_TRANSFER_ONLY:
        phase_keywords.append(("transfer only", "Transfer Only"))
    if ALERT_LIMIT_ONLY:
        phase_keywords.append(("limit only", "Limit Only"))
    if ALERT_AUCTION:
        phase_keywords.append(("auction", "Auction"))
    if ALERT_FULL_TRADING:
        phase_keywords.append(("full trading", "Full Trading"))

    return phase_keywords


def extract_confirmed_lines_from_text(text: str):
    """
    Pull lines that contain confirmed Coinbase rollout phases.
    """
    phase_keywords = get_enabled_phase_keywords()
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    results = []

    for line in lines:
        low = normalize_text(line)
        for needle, phase_name in phase_keywords:
            if needle in low:
                results.append(
                    {
                        "phase": phase_name,
                        "raw": line.strip(),
                    }
                )
                break

    return results


def parse_asset_symbol_from_text(raw_text: str) -> str:
    """
    Examples:
    - "ABC-USD will enter transfer-only mode..."
    - "Our XYZ-USD order book is now in full-trading mode..."
    """
    if not raw_text:
        return "UNKNOWN"

    m = re.search(r"\b([A-Z0-9]{2,20}-USD)\b", raw_text)
    if m:
        return m.group(1)

    # fallback for bare ticker-like uppercase tokens
    candidates = re.findall(r"\b[A-Z0-9]{2,15}\b", raw_text)
    blacklist = {
        "USD", "API", "DEX", "UTC", "ONLY", "FULL", "LIMIT", "MODE",
        "OUR", "AND", "THE", "WILL", "NOW", "ON", "IN", "IS"
    }
    for c in candidates:
        if c not in blacklist:
            return c

    return "UNKNOWN"


def fetch_coinbase_status_page_events():
    events = []

    if BeautifulSoup is None:
        log("beautifulsoup4 is not installed; Coinbase status parsing disabled.")
        return events

    try:
        r = SESSION.get(COINBASE_STATUS_URL, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n")
        matches = extract_confirmed_lines_from_text(text)

        for match in matches:
            events.append(
                {
                    "source": "Coinbase Exchange Status",
                    "phase": match["phase"],
                    "raw": match["raw"],
                }
            )
    except Exception as e:
        log(f"Coinbase status fetch error: {e}")

    return events


def fetch_coinbase_blog_feed_events():
    """
    Optional secondary source.
    Keeps logic phase-based only.
    """
    events = []

    if BeautifulSoup is None:
        return events

    try:
        r = SESSION.get(COINBASE_MARKETS_RSS_URL, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "xml")
        items = soup.find_all("item")

        for item in items[:30]:
            title = item.title.text.strip() if item.title and item.title.text else ""
            description = item.description.text.strip() if item.description and item.description.text else ""
            combined = f"{title}\n{description}"

            matches = extract_confirmed_lines_from_text(combined)
            for match in matches:
                events.append(
                    {
                        "source": "Coinbase Blog Feed",
                        "phase": match["phase"],
                        "raw": match["raw"],
                    }
                )
    except Exception as e:
        log(f"Coinbase RSS fetch error: {e}")

    return events


def dedupe_events(events):
    unique = []
    seen = set()

    for ev in events:
        asset = parse_asset_symbol_from_text(ev.get("raw", ""))
        phase = ev.get("phase", "UNKNOWN")
        raw = normalize_text(ev.get("raw", ""))
        key = f"{asset}|{phase}|{raw}"

        if key in seen:
            continue
        seen.add(key)

        ev["asset"] = asset
        unique.append(ev)

    return unique


def check_confirmed_coinbase_listings():
    events = []
    events.extend(fetch_coinbase_status_page_events())
    events.extend(fetch_coinbase_blog_feed_events())

    events = dedupe_events(events)

    if not events:
        log("No confirmed Coinbase listing phase events found.")
        return

    alerts_sent = 0

    for ev in events:
        key = f"{ev['asset']}|{ev['phase']}"
        if key in seen_listings:
            continue

        seen_listings.add(key)
        alerts_sent += 1

        msg = (
            f"📢 CONFIRMED COINBASE LISTING UPDATE\n\n"
            f"Asset: {ev['asset']}\n"
            f"Phase: {ev['phase']}\n"
            f"Source: {ev['source']}\n\n"
            f"Official text:\n{ev['raw']}\n\n"
            f"Reference:\n{COINBASE_STATUS_URL}"
        )
        send_telegram(msg)

    log(f"Confirmed Coinbase listing check complete. Alerts sent: {alerts_sent}")


# ============================================================
# MAIN
# ============================================================

def startup_banner():
    log("BOT STARTED")
    log(f"SCAN_INTERVAL={SCAN_INTERVAL}")
    log(f"LISTING_CHECK_INTERVAL={LISTING_CHECK_INTERVAL}")
    log(f"MIN_LIQUIDITY={MIN_LIQUIDITY}")
    log(f"MIN_VOLUME_5M={MIN_VOLUME_5M}")
    log(f"BUY_SELL_RATIO_THRESHOLD={BUY_SELL_RATIO_THRESHOLD}")
    log(f"BOTTOM_RANGE_PCT={BOTTOM_RANGE_PCT}")
    log(
        "COINBASE ALERT PHASES="
        f"transfer_only={ALERT_TRANSFER_ONLY}, "
        f"limit_only={ALERT_LIMIT_ONLY}, "
        f"auction={ALERT_AUCTION}, "
        f"full_trading={ALERT_FULL_TRADING}"
    )


def main():
    startup_banner()
    last_listing_check = 0.0

    while True:
        try:
            scan_base()

            now_ts = time.time()
            if (now_ts - last_listing_check) >= LISTING_CHECK_INTERVAL:
                check_confirmed_coinbase_listings()
                last_listing_check = now_ts

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log("Bot stopped by user.")
            break
        except Exception as e:
            log(f"Main loop error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
