import os
import re
import sys
import time
import threading
import subprocess
from typing import Dict, List, Set, Tuple, Optional
from urllib.parse import urljoin, urlparse

def install(package: str):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import requests
except Exception:
    install("requests")
    import requests

try:
    from bs4 import BeautifulSoup
except Exception:
    install("beautifulsoup4")
    from bs4 import BeautifulSoup


# =========================
# CONFIG
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))  # seconds
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_PAGES_PER_SOURCE = int(os.getenv("MAX_PAGES_PER_SOURCE", "12"))
MAX_CRAWL_DEPTH = int(os.getenv("MAX_CRAWL_DEPTH", "2"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "21600"))  # 6h
MIN_DOC_SCORE = float(os.getenv("MIN_DOC_SCORE", "4.0"))

MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "5000"))
MIN_VOLUME_M5_USD = float(os.getenv("MIN_VOLUME_M5_USD", "500"))
MIN_BUYS_M5 = int(os.getenv("MIN_BUYS_M5", "3"))

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"

USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (compatible; DocDexScanner/1.0)")

DOC_SOURCES = [
    # Add project docs / launchpads / discovery pages here
    "https://coinmarketcap.com/new/",
    "https://www.coingecko.com/en/new-cryptocurrencies",
]

# =========================
# HTTP
# =========================
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# =========================
# REGEX / TERMS
# =========================
CONTRACT_REGEX = re.compile(r"0x[a-fA-F0-9]{40}")

DATE_REGEX = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+\d{1,2}(?:,\s+\d{4})?\b",
    re.IGNORECASE,
)

BULLISH_WORDS = {
    "launch": 1.2,
    "mainnet": 1.5,
    "testnet": 0.7,
    "bridge": 1.2,
    "listing": 1.3,
    "listed": 1.0,
    "partnership": 1.2,
    "integration": 1.1,
    "roadmap": 0.8,
    "tokenomics": 1.0,
    "utility": 0.8,
    "staking": 0.9,
    "governance": 0.7,
    "airdrop": 0.2,
    "whitepaper": 0.8,
    "docs": 0.5,
    "github": 1.0,
    "audit": 1.5,
    "audited": 1.5,
    "ecosystem": 0.5,
    "presale": 0.4,
    "ido": 0.6,
    "launchpad": 0.7,
    "token generation event": 1.3,
    "tge": 1.1,
    "bridge live": 1.5,
    "exchange": 0.9,
    "dex": 0.6,
    "community": 0.4,
    "real yield": 0.8,
}

BEARISH_WORDS = {
    "100x": 2.0,
    "1000x": 2.5,
    "guaranteed": 2.0,
    "guaranteed profit": 2.5,
    "instant gains": 2.0,
    "no risk": 2.5,
    "free money": 2.5,
    "moon soon": 1.8,
    "next pepe": 1.5,
    "easy money": 2.0,
    "pump": 1.0,
    "pump group": 2.0,
    "hidden gem": 0.8,
    "buy now": 0.8,
    "ape now": 1.0,
    "financial freedom": 1.8,
    "passive income guaranteed": 2.5,
}

POSITIVE_STRUCTURAL_HINTS = {
    "team": 0.5,
    "founder": 0.5,
    "about us": 0.4,
    "faq": 0.3,
    "privacy policy": 0.2,
    "terms of service": 0.2,
    "contact": 0.3,
    "documentation": 0.6,
}

NEGATIVE_STRUCTURAL_HINTS = {
    "lorem ipsum": 2.0,
    "coming soon": 0.8,
    "under construction": 1.0,
}

# =========================
# STATE
# =========================
LOCK = threading.Lock()
SEND_LOCK = threading.Lock()
LAST_ALERT_TS: Dict[str, float] = {}

# =========================
# TELEGRAM
# =========================
def send(msg: str):
    with SEND_LOCK:
        text = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{msg}"

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print(text)
            return

        try:
            r = SESSION.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if r.status_code == 200:
                print("Telegram delivered")
            else:
                print(f"Telegram error {r.status_code}: {r.text[:300]}")
        except Exception as e:
            print("Telegram send error:", e)

        print(text)

# =========================
# HELPERS
# =========================
def safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default

def safe_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return default

def now_ts() -> float:
    return time.time()

def should_alert(key: str) -> bool:
    with LOCK:
        last = LAST_ALERT_TS.get(key, 0.0)
        if now_ts() - last < ALERT_COOLDOWN_SECONDS:
            return False
        LAST_ALERT_TS[key] = now_ts()
        return True

def normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())

def same_domain(url_a: str, url_b: str) -> bool:
    try:
        return urlparse(url_a).netloc == urlparse(url_b).netloc
    except Exception:
        return False

# =========================
# FETCH / PARSE
# =========================
def fetch_html(url: str) -> Optional[str]:
    try:
        r = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None

        ctype = (r.headers.get("content-type") or "").lower()
        if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
            return None

        return r.text
    except Exception:
        return None

def extract_visible_text_and_links(base_url: str, html: str) -> Tuple[str, List[str], str]:
    try:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        text = normalize_text(soup.get_text(" "))

        links: List[str] = []
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            joined = urljoin(base_url, href)

            if not joined.startswith("http"):
                continue

            if same_domain(base_url, joined):
                links.append(joined)

        # preserve order, remove dupes
        seen = set()
        uniq_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                uniq_links.append(link)

        return text, uniq_links, title
    except Exception:
        return "", [], ""

# =========================
# DOC ANALYSIS
# =========================
def score_text(text: str) -> Tuple[float, Dict[str, float]]:
    score = 0.0
    details: Dict[str, float] = {}

    for phrase, weight in BULLISH_WORDS.items():
        if phrase in text:
            score += weight
            details[f"+ {phrase}"] = weight

    for phrase, weight in BEARISH_WORDS.items():
        if phrase in text:
            score -= weight
            details[f"- {phrase}"] = -weight

    for phrase, weight in POSITIVE_STRUCTURAL_HINTS.items():
        if phrase in text:
            score += weight
            details[f"+ {phrase}"] = weight

    for phrase, weight in NEGATIVE_STRUCTURAL_HINTS.items():
        if phrase in text:
            score -= weight
            details[f"- {phrase}"] = -weight

    contracts = CONTRACT_REGEX.findall(text)
    if contracts:
        bonus = min(2.5, 1.5 + (0.2 * len(set(contracts))))
        score += bonus
        details["+ contract present"] = bonus
    else:
        penalty = -2.0
        score += penalty
        details["- no contract"] = penalty

    dates_found = DATE_REGEX.findall(text)
    if dates_found:
        score += 0.6
        details["+ dated roadmap/news hint"] = 0.6

    if len(text) > 5000:
        score += 0.6
        details["+ substantial text"] = 0.6
    elif len(text) > 2000:
        score += 0.3
        details["+ decent text depth"] = 0.3
    else:
        score -= 0.5
        details["- thin content"] = -0.5

    return score, details

def crawl_source(source_url: str) -> Dict[str, dict]:
    """
    Returns per-contract aggregated document findings.
    """
    queue: List[Tuple[str, int]] = [(source_url, 0)]
    visited: Set[str] = set()
    pages_visited = 0

    contracts_found: Dict[str, dict] = {}

    while queue and pages_visited < MAX_PAGES_PER_SOURCE:
        url, depth = queue.pop(0)

        if url in visited:
            continue
        visited.add(url)

        html = fetch_html(url)
        if not html:
            continue

        text, links, title = extract_visible_text_and_links(url, html)
        if not text:
            continue

        pages_visited += 1

        page_score, score_details = score_text(text)
        contracts = list(set(CONTRACT_REGEX.findall(text)))

        for contract in contracts:
            entry = contracts_found.get(contract)
            if not entry:
                entry = {
                    "contract": contract,
                    "pages": [],
                    "source_roots": set(),
                    "doc_score": 0.0,
                    "titles": [],
                }
                contracts_found[contract] = entry

            entry["pages"].append(url)
            entry["source_roots"].add(source_url)
            entry["doc_score"] += page_score
            if title:
                entry["titles"].append(title)

        if depth < MAX_CRAWL_DEPTH:
            for link in links:
                if link not in visited:
                    queue.append((link, depth + 1))

    return contracts_found

# =========================
# DEX CHECK
# =========================
def get_best_dex_data(contract: str) -> Optional[dict]:
    try:
        r = SESSION.get(DEX_API.format(contract), timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None

        data = r.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return None

        best = None
        best_score = None

        for pair in pairs:
            liquidity = safe_float((pair.get("liquidity") or {}).get("usd"))
            vol_m5 = safe_float((pair.get("volume") or {}).get("m5"))
            buys_m5 = safe_int(((pair.get("txns") or {}).get("m5") or {}).get("buys"))
            sells_m5 = safe_int(((pair.get("txns") or {}).get("m5") or {}).get("sells"))
            price = safe_float(pair.get("priceUsd"))
            market_cap = safe_float(pair.get("marketCap"))
            fdv = safe_float(pair.get("fdv"))
            dex_id = pair.get("dexId") or ""
            pair_url = pair.get("url") or ""
            base_symbol = ((pair.get("baseToken") or {}).get("symbol") or "UNK").upper()
            chain_id = (pair.get("chainId") or "").lower()

            score = liquidity * 0.0003 + vol_m5 * 0.002 + buys_m5 * 4 - sells_m5 * 1.5

            if best is None or score > best_score:
                best = {
                    "price_usd": price,
                    "liquidity_usd": liquidity,
                    "volume_m5_usd": vol_m5,
                    "buys_m5": buys_m5,
                    "sells_m5": sells_m5,
                    "market_cap": market_cap,
                    "fdv": fdv,
                    "dex_id": dex_id,
                    "url": pair_url,
                    "symbol": base_symbol,
                    "chain_id": chain_id,
                }
                best_score = score

        return best
    except Exception:
        return None

def score_market(dex: dict) -> Tuple[float, Dict[str, float]]:
    score = 0.0
    details: Dict[str, float] = {}

    liquidity = safe_float(dex.get("liquidity_usd"))
    vol_m5 = safe_float(dex.get("volume_m5_usd"))
    buys_m5 = safe_int(dex.get("buys_m5"))
    sells_m5 = safe_int(dex.get("sells_m5"))

    if liquidity >= MIN_LIQUIDITY_USD:
        score += 2.0
        details["+ min liquidity passed"] = 2.0
    else:
        penalty = -3.0
        score += penalty
        details["- low liquidity"] = penalty

    if vol_m5 >= MIN_VOLUME_M5_USD:
        score += 1.5
        details["+ m5 volume passed"] = 1.5
    else:
        penalty = -1.5
        score += penalty
        details["- low m5 volume"] = penalty

    if buys_m5 >= MIN_BUYS_M5:
        score += 1.2
        details["+ m5 buys active"] = 1.2
    else:
        penalty = -1.0
        score += penalty
        details["- weak buy activity"] = penalty

    ratio = (buys_m5 + 1) / max(sells_m5 + 1, 1)
    if ratio >= 1.5:
        score += 1.0
        details["+ buy/sell ratio bullish"] = 1.0
    elif ratio < 0.8:
        penalty = -1.2
        score += penalty
        details["- buy/sell ratio weak"] = penalty

    return score, details

# =========================
# MAIN ANALYSIS
# =========================
def analyze_contract(contract_entry: dict):
    contract = contract_entry["contract"]
    doc_score = float(contract_entry.get("doc_score", 0.0))
    page_count = len(contract_entry.get("pages", []))

    if page_count >= 2:
        doc_score += 0.8
    if page_count >= 4:
        doc_score += 0.6

    dex = get_best_dex_data(contract)
    if not dex:
        return

    market_score, _ = score_market(dex)
    total_score = doc_score + market_score

    if doc_score < MIN_DOC_SCORE:
        return

    if dex["liquidity_usd"] < MIN_LIQUIDITY_USD:
        return

    if dex["volume_m5_usd"] < MIN_VOLUME_M5_USD:
        return

    if total_score < (MIN_DOC_SCORE + 1.5):
        return

    alert_key = contract.lower()
    if not should_alert(alert_key):
        return

    titles = contract_entry.get("titles", [])[:3]
    pages = contract_entry.get("pages", [])[:3]

    title_block = ""
    if titles:
        title_block = "Titles:\n" + "\n".join(f"- {t[:80]}" for t in titles) + "\n\n"

    pages_block = ""
    if pages:
        pages_block = "Pages:\n" + "\n".join(f"- {p}" for p in pages) + "\n\n"

    send(
        f"🔥 DOC + DEX BULLISH SIGNAL\n\n"
        f"Symbol: {dex.get('symbol', 'UNK')}\n"
        f"Chain: {dex.get('chain_id', 'unknown')}\n"
        f"Contract: {contract}\n\n"
        f"Doc Score: {doc_score:.2f}\n"
        f"Market Score: {market_score:.2f}\n"
        f"Total Score: {total_score:.2f}\n\n"
        f"Liquidity: ${dex['liquidity_usd']:,.0f}\n"
        f"5m Volume: ${dex['volume_m5_usd']:,.0f}\n"
        f"5m Buys: {dex['buys_m5']}\n"
        f"5m Sells: {dex['sells_m5']}\n"
        f"Price: ${dex['price_usd']:.10f}\n"
        f"MC: ${dex['market_cap']:,.0f}\n"
        f"FDV: ${dex['fdv']:,.0f}\n"
        f"Dex: {dex.get('dex_id', '')}\n"
        f"Pair URL: {dex.get('url', '')}\n\n"
        f"{title_block}"
        f"{pages_block}"
        f"Reason: strong document signals plus tradable Dex activity"
    )

def scanner_loop():
    send(
        f"🚀 DOC + DEX SCANNER STARTED\n\n"
        f"Sources: {len(DOC_SOURCES)}\n"
        f"Check Interval: {CHECK_INTERVAL}s\n"
        f"Max Pages/Source: {MAX_PAGES_PER_SOURCE}\n"
        f"Max Crawl Depth: {MAX_CRAWL_DEPTH}\n"
        f"Min Doc Score: {MIN_DOC_SCORE:.2f}\n"
        f"Min Liquidity: ${MIN_LIQUIDITY_USD:,.0f}\n"
        f"Min 5m Volume: ${MIN_VOLUME_M5_USD:,.0f}\n"
        f"Min 5m Buys: {MIN_BUYS_M5}"
    )

    while True:
        try:
            aggregated: Dict[str, dict] = {}

            for source in DOC_SOURCES:
                print(f"Scanning source: {source}")
                found = crawl_source(source)

                for contract, entry in found.items():
                    existing = aggregated.get(contract)
                    if not existing:
                        aggregated[contract] = entry
                    else:
                        existing["pages"].extend(entry.get("pages", []))
                        existing["source_roots"] = existing.get("source_roots", set()) | entry.get("source_roots", set())
                        existing["doc_score"] += entry.get("doc_score", 0.0)
                        existing["titles"].extend(entry.get("titles", []))

            for contract, entry in aggregated.items():
                # dedupe lists while preserving order
                entry["pages"] = list(dict.fromkeys(entry.get("pages", [])))
                entry["titles"] = list(dict.fromkeys(entry.get("titles", [])))
                analyze_contract(entry)

        except Exception as e:
            print("scanner_loop error:", e)

        time.sleep(CHECK_INTERVAL)

# =========================
# START
# =========================
if __name__ == "__main__":
    scanner_loop()
