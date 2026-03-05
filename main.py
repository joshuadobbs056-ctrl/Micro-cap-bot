#!/usr/bin/env python3
"""
SHORT-ONLY Futures Bot (Runner Breakdown Hunter)
- Exchange via ccxt (default: BYBIT linear USDT perpetual)
- Short entries only (no longs)
- Breakdown + volume spike + downtrend filter
- Risk controls: hard SL, take-profit, trailing stop, cooldown, time-stop
- Paper mode (DRY_RUN=1) with ledger + state persistence
- Telegram alerts (optional) with separate PROFIT_CHAT_ID / LOSS_CHAT_ID (set custom sounds per chat in Telegram)

DISCLAIMER: This is educational code. Futures are high-risk. Test on paper/sandbox first.
"""

import os, time, json, math, traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

# ----------------------------
# Optional deps
#   pip install ccxt requests
# ----------------------------
import ccxt
import requests

STATE_FILE = os.getenv("STATE_FILE", "short_bot_state.json")
LEDGER_FILE = os.getenv("LEDGER_FILE", "short_bot_ledger.jsonl")

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v is not None and str(v).strip() != "" else default

def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v is not None and str(v).strip() != "" else default

def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None else default

def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

# ----------------------------
# Telegram
# ----------------------------
class Telegram:
    def __init__(self):
        self.token = env_str("TELEGRAM_BOT_TOKEN", "")
        self.base_chat = env_str("TELEGRAM_CHAT_ID", "")
        self.profit_chat = env_str("PROFIT_CHAT_ID", "")  # optional separate chat for profit sound
        self.loss_chat = env_str("LOSS_CHAT_ID", "")      # optional separate chat for loss sound
        self.enabled = bool(self.token and (self.base_chat or self.profit_chat or self.loss_chat))

    def send(self, text: str, chat_id: Optional[str] = None):
        if not self.enabled:
            return
        cid = chat_id or self.base_chat
        if not cid:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": cid, "text": text}, timeout=10)
        except Exception:
            pass

    def send_profit(self, text: str):
        self.send(text, self.profit_chat or self.base_chat)

    def send_loss(self, text: str):
        self.send(text, self.loss_chat or self.base_chat)

# ----------------------------
# Indicators
# ----------------------------
def ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        ch = values[i] - values[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses += -ch
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))

def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / max(1, len(trs))
    return sum(trs[-period:]) / period

# ----------------------------
# Config
# ----------------------------
@dataclass
class Config:
    EXCHANGE: str = env_str("EXCHANGE", "bybit")  # bybit | okx | binance
    API_KEY: str = env_str("API_KEY", "")
    API_SECRET: str = env_str("API_SECRET", "")
    API_PASSWORD: str = env_str("API_PASSWORD", "")  # okx uses password
    SANDBOX: bool = env_bool("SANDBOX", False)        # if exchange supports
    DRY_RUN: bool = env_bool("DRY_RUN", True)

    # Market universe
    QUOTE: str = env_str("QUOTE", "USDT")
    SYMBOLS: str = env_str("SYMBOLS", "")  # comma-separated. If blank, uses TOP_N by volume
    TOP_N: int = env_int("TOP_N", 25)
    EXCLUDE: str = env_str("EXCLUDE", "BTC/USDT,ETH/USDT")  # comma-separated

    # Timeframes
    TF: str = env_str("TF", "5m")
    LOOKBACK_BARS: int = env_int("LOOKBACK_BARS", 120)

    # Entry logic (SHORT ONLY)
    BREAKDOWN_LOOKBACK: int = env_int("BREAKDOWN_LOOKBACK", 20)  # range support lookback
    BREAKDOWN_BUFFER_PCT: float = env_float("BREAKDOWN_BUFFER_PCT", 0.15)  # below support by %
    RVOL_PERIOD: int = env_int("RVOL_PERIOD", 20)
    RVOL_THRESHOLD: float = env_float("RVOL_THRESHOLD", 2.5)
    PRICE_DROP_1BAR_PCT: float = env_float("PRICE_DROP_1BAR_PCT", 0.4)  # last bar red by %
    TREND_EMA_FAST: int = env_int("TREND_EMA_FAST", 20)
    TREND_EMA_SLOW: int = env_int("TREND_EMA_SLOW", 50)
    RSI_MAX: float = env_float("RSI_MAX", 55.0)  # only short if RSI <= this

    # Risk/position sizing
    LEVERAGE: int = env_int("LEVERAGE", 3)  # keep low
    MAX_OPEN_TRADES: int = env_int("MAX_OPEN_TRADES", 5)
    RISK_PER_TRADE_PCT: float = env_float("RISK_PER_TRADE_PCT", 0.7)  # % of equity at risk per trade (based on SL)
    MIN_TRADE_USD: float = env_float("MIN_TRADE_USD", 20.0)
    MAX_TRADE_USD: float = env_float("MAX_TRADE_USD", 300.0)

    # Exits
    STOP_LOSS_PCT: float = env_float("STOP_LOSS_PCT", 3.0)
    TAKE_PROFIT_PCT: float = env_float("TAKE_PROFIT_PCT", 7.0)
    TRAIL_START_PCT: float = env_float("TRAIL_START_PCT", 5.0)
    TRAIL_ATR_MULT: float = env_float("TRAIL_ATR_MULT", 1.2)  # trailing based on ATR
    TRAIL_MIN_PCT: float = env_float("TRAIL_MIN_PCT", 1.0)
    TRAIL_MAX_PCT: float = env_float("TRAIL_MAX_PCT", 6.0)
    TIME_STOP_MIN: int = env_int("TIME_STOP_MIN", 60)  # close if not hit TP by this many minutes
    COOLDOWN_MIN: int = env_int("COOLDOWN_MIN", 60)    # after any exit, wait before re-entry

    # Safety
    MARKET_GUARD_SYMBOL: str = env_str("MARKET_GUARD_SYMBOL", "BTC/USDT")
    GUARD_TF: str = env_str("GUARD_TF", "15m")
    GUARD_DROP_PCT: float = env_float("GUARD_DROP_PCT", -1.2)  # if BTC drops more than this in TF window, block new shorts? (you can invert)
    GUARD_RIP_PCT: float = env_float("GUARD_RIP_PCT", 1.0)     # if BTC pumps more than this, block NEW shorts
    STATUS_INTERVAL_SEC: int = env_int("STATUS_INTERVAL_SEC", 60)

# ----------------------------
# State
# ----------------------------
@dataclass
class Position:
    symbol: str
    side: str  # "short"
    qty: float
    entry: float
    entry_ts: float
    stop: float
    tp: float
    high_water: float  # for shorts: best favorable move is lowest price; we track low_water instead
    low_water: float
    trailing_active: bool
    trail_dist_pct: float

@dataclass
class BotState:
    positions: Dict[str, Position]
    cooldown_until: Dict[str, float]
    realized_pnl: float
    wins: int
    losses: int

def load_state() -> BotState:
    if not os.path.exists(STATE_FILE):
        return BotState(positions={}, cooldown_until={}, realized_pnl=0.0, wins=0, losses=0)
    try:
        raw = json.load(open(STATE_FILE, "r"))
        pos = {}
        for sym, p in raw.get("positions", {}).items():
            pos[sym] = Position(**p)
        return BotState(
            positions=pos,
            cooldown_until=raw.get("cooldown_until", {}),
            realized_pnl=float(raw.get("realized_pnl", 0.0)),
            wins=int(raw.get("wins", 0)),
            losses=int(raw.get("losses", 0)),
        )
    except Exception:
        return BotState(positions={}, cooldown_until={}, realized_pnl=0.0, wins=0, losses=0)

def save_state(state: BotState):
    raw = {
        "positions": {k: asdict(v) for k, v in state.positions.items()},
        "cooldown_until": state.cooldown_until,
        "realized_pnl": state.realized_pnl,
        "wins": state.wins,
        "losses": state.losses,
        "ts": now_utc_iso(),
    }
    with open(STATE_FILE, "w") as f:
        json.dump(raw, f, indent=2)

def append_ledger(event: Dict[str, Any]):
    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")

# ----------------------------
# Exchange wrapper
# ----------------------------
class Ex:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ex = self._make_exchange()
        self.markets = {}
        self._load_markets()

    def _make_exchange(self):
        name = self.cfg.EXCHANGE.lower().strip()
        if name not in ("bybit", "okx", "binance"):
            raise ValueError("EXCHANGE must be bybit, okx, or binance")
        cls = getattr(ccxt, name)
        params = {"enableRateLimit": True}
        if self.cfg.API_KEY and self.cfg.API_SECRET:
            params["apiKey"] = self.cfg.API_KEY
            params["secret"] = self.cfg.API_SECRET
        if name == "okx" and self.cfg.API_PASSWORD:
            params["password"] = self.cfg.API_PASSWORD
        ex = cls(params)
        # Prefer USDT linear swaps
        if name == "bybit":
            ex.options["defaultType"] = "swap"
        if name == "binance":
            ex.options["defaultType"] = "future"
        if name == "okx":
            ex.options["defaultType"] = "swap"
        if self.cfg.SANDBOX and hasattr(ex, "set_sandbox_mode"):
            ex.set_sandbox_mode(True)
        return ex

    def _load_markets(self):
        self.markets = self.ex.load_markets()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> List[List[float]]:
        return self.ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.ex.fetch_ticker(symbol)

    def fetch_balance(self) -> Dict[str, Any]:
        return self.ex.fetch_balance()

    def set_leverage(self, symbol: str, lev: int):
        try:
            if hasattr(self.ex, "set_leverage"):
                self.ex.set_leverage(lev, symbol)
        except Exception:
            pass

    def create_market_order(self, symbol: str, side: str, amount: float):
        return self.ex.create_order(symbol, "market", side, amount)

    def market_min_amount(self, symbol: str) -> float:
        m = self.markets.get(symbol, {})
        limits = m.get("limits", {})
        amt = limits.get("amount", {})
        return float(amt.get("min", 0.0) or 0.0)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.ex.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.ex.price_to_precision(symbol, price))

# ----------------------------
# Universe selection
# ----------------------------
def parse_symbols(cfg: Config) -> List[str]:
    if cfg.SYMBOLS.strip():
        return [s.strip() for s in cfg.SYMBOLS.split(",") if s.strip()]
    return []

def build_universe(ex: Ex, cfg: Config) -> List[str]:
    excl = set([s.strip() for s in cfg.EXCLUDE.split(",") if s.strip()])
    if cfg.SYMBOLS.strip():
        syms = [s.strip() for s in cfg.SYMBOLS.split(",") if s.strip()]
        return [s for s in syms if s in ex.markets and s not in excl]

    # Top N by quote volume (best-effort based on tickers)
    tickers = ex.ex.fetch_tickers()
    candidates = []
    for sym, t in tickers.items():
        if sym in excl:
            continue
        if f"/{cfg.QUOTE}" not in sym:
            continue
        m = ex.markets.get(sym)
        if not m:
            continue
        # prefer swaps/futures
        if not (m.get("swap") or m.get("future")):
            continue
        qv = t.get("quoteVolume") or 0.0
        last = t.get("last") or 0.0
        if qv and last:
            candidates.append((float(qv), sym))
    candidates.sort(reverse=True)
    return [sym for _, sym in candidates[: cfg.TOP_N]]

# ----------------------------
# Strategy checks
# ----------------------------
def market_guard(ex: Ex, cfg: Config) -> Tuple[bool, str]:
    """Blocks NEW shorts if BTC is ripping; optionally blocks if BTC dumping (you decide)."""
    try:
        o = ex.fetch_ohlcv(cfg.MARKET_GUARD_SYMBOL, cfg.GUARD_TF, 2)
        if len(o) < 2:
            return True, "guard:insufficient"
        prev_close = o[-2][4]
        last_close = o[-1][4]
        chg = (last_close - prev_close) / prev_close * 100.0
        if chg >= cfg.GUARD_RIP_PCT:
            return False, f"guard:block_rip {chg:.2f}%/{cfg.GUARD_TF}"
        if chg <= cfg.GUARD_DROP_PCT:
            # You can flip this to block too; leaving it as allow by default.
            return True, f"guard:ok_dump {chg:.2f}%/{cfg.GUARD_TF}"
        return True, f"guard:ok {chg:.2f}%/{cfg.GUARD_TF}"
    except Exception:
        return True, "guard:error_ok"

def compute_rvol(vols: List[float], period: int) -> float:
    if len(vols) < period + 1:
        return 1.0
    avg = sum(vols[-(period+1):-1]) / period
    if avg <= 0:
        return 1.0
    return vols[-1] / avg

def short_entry_signal(ohlcv: List[List[float]], cfg: Config) -> Tuple[bool, Dict[str, float]]:
    """
    SHORT ONLY signal:
    - Downtrend: EMA(fast) < EMA(slow)
    - Breakdown: close < min(low last N) by buffer
    - Volume spike: RVOL >= threshold
    - Momentum: last candle down by PRICE_DROP_1BAR_PCT
    - RSI <= RSI_MAX
    """
    if len(ohlcv) < max(cfg.LOOKBACK_BARS, cfg.BREAKDOWN_LOOKBACK, cfg.RVOL_PERIOD, cfg.TREND_EMA_SLOW) + 5:
        return False, {}

    highs = [c[2] for c in ohlcv]
    lows  = [c[3] for c in ohlcv]
    closes= [c[4] for c in ohlcv]
    vols  = [c[5] for c in ohlcv]

    efast = ema(closes[-(cfg.TREND_EMA_FAST*3):], cfg.TREND_EMA_FAST)
    eslow = ema(closes[-(cfg.TREND_EMA_SLOW*3):], cfg.TREND_EMA_SLOW)
    downtrend = efast < eslow

    r = rsi(closes, 14)
    r_ok = r <= cfg.RSI_MAX

    support = min(lows[-cfg.BREAKDOWN_LOOKBACK:])
    last_close = closes[-1]
    buffer = support * (cfg.BREAKDOWN_BUFFER_PCT / 100.0)
    breakdown = last_close < (support - buffer)

    rvol = compute_rvol(vols, cfg.RVOL_PERIOD)
    vol_ok = rvol >= cfg.RVOL_THRESHOLD

    last_open = ohlcv[-1][1]
    last_pct = (last_close - last_open) / max(1e-12, last_open) * 100.0
    drop_ok = last_pct <= -abs(cfg.PRICE_DROP_1BAR_PCT)

    ok = downtrend and r_ok and breakdown and vol_ok and drop_ok
    metrics = {
        "efast": efast, "eslow": eslow,
        "rsi": r,
        "support": support,
        "last_close": last_close,
        "rvol": rvol,
        "last_candle_pct": last_pct
    }
    return ok, metrics

# ----------------------------
# Sizing / Orders
# ----------------------------
def get_equity_usdt(ex: Ex, cfg: Config) -> float:
    bal = ex.fetch_balance()
    # Best-effort: use total/free USDT
    total = bal.get("total", {}).get(cfg.QUOTE)
    free = bal.get("free", {}).get(cfg.QUOTE)
    if total is None and free is None:
        # fallback: search
        for k in ("USDT", cfg.QUOTE):
            if k in bal.get("total", {}):
                total = bal["total"][k]
                break
    return float(total or free or 0.0)

def calc_position_usd(cfg: Config, equity: float, entry: float) -> float:
    """
    Risk-based sizing: risk_per_trade_pct of equity is the max loss at STOP_LOSS_PCT.
    position_usd = (equity * risk%) / (SL%).
    Then clamp by MIN/MAX_TRADE_USD.
    """
    risk_usd = equity * (cfg.RISK_PER_TRADE_PCT / 100.0)
    sl_frac = cfg.STOP_LOSS_PCT / 100.0
    if sl_frac <= 0:
        sl_frac = 0.03
    pos = risk_usd / sl_frac
    return clamp(pos, cfg.MIN_TRADE_USD, cfg.MAX_TRADE_USD)

def usd_to_qty(ex: Ex, symbol: str, usd: float, price: float) -> float:
    qty = usd / max(1e-12, price)
    qty = ex.amount_to_precision(symbol, qty)
    return float(qty)

# ----------------------------
# Position management
# ----------------------------
def build_position(cfg: Config, symbol: str, qty: float, entry: float, entry_ts: float, atr_val: float) -> Position:
    stop = entry * (1.0 + cfg.STOP_LOSS_PCT / 100.0)  # for shorts, stop above entry
    tp   = entry * (1.0 - cfg.TAKE_PROFIT_PCT / 100.0)
    # ATR trailing distance percent (dynamic)
    if atr_val > 0:
        trail_pct = (atr_val / entry) * 100.0 * cfg.TRAIL_ATR_MULT
    else:
        trail_pct = cfg.TRAIL_MIN_PCT
    trail_pct = clamp(trail_pct, cfg.TRAIL_MIN_PCT, cfg.TRAIL_MAX_PCT)
    return Position(
        symbol=symbol, side="short", qty=qty,
        entry=entry, entry_ts=entry_ts,
        stop=stop, tp=tp,
        high_water=entry, low_water=entry,
        trailing_active=False,
        trail_dist_pct=trail_pct
    )

def should_time_stop(cfg: Config, pos: Position, now_ts: float) -> bool:
    mins = (now_ts - pos.entry_ts) / 60.0
    return mins >= cfg.TIME_STOP_MIN

def unrealized_pnl_short(entry: float, price: float, qty: float) -> float:
    # PnL in quote currency approximated
    return (entry - price) * qty

def manage_position(ex: Ex, cfg: Config, tg: Telegram, state: BotState, pos: Position, last_price: float):
    now_ts = time.time()
    sym = pos.symbol

    # Update favorable move (for shorts: low_water is best)
    pos.low_water = min(pos.low_water, last_price)

    # Activate trailing after TRAIL_START_PCT profit
    pnl_pct = (pos.entry - last_price) / pos.entry * 100.0
    if (not pos.trailing_active) and pnl_pct >= cfg.TRAIL_START_PCT:
        pos.trailing_active = True

    # Trailing stop price for shorts moves down with price: stop_trail = low_water * (1 + trail_dist%)
    trail_stop = None
    if pos.trailing_active:
        trail_stop = pos.low_water * (1.0 + pos.trail_dist_pct / 100.0)

    # Exit conditions
    hit_stop = last_price >= pos.stop
    hit_tp = last_price <= pos.tp
    hit_trail = (trail_stop is not None) and (last_price >= trail_stop)
    hit_time = should_time_stop(cfg, pos, now_ts)

    reason = None
    if hit_stop:
        reason = "STOP"
    elif hit_tp:
        reason = "TP"
    elif hit_trail:
        reason = "TRAIL"
    elif hit_time and pnl_pct > 0:
        reason = "TIME_PROFIT"
    elif hit_time and pnl_pct <= 0:
        reason = "TIME_EXIT"

    if reason:
        close_short(ex, cfg, tg, state, pos, last_price, reason)
        # cooldown
        state.cooldown_until[sym] = now_ts + cfg.COOLDOWN_MIN * 60
        if sym in state.positions:
            del state.positions[sym]

def close_short(ex: Ex, cfg: Config, tg: Telegram, state: BotState, pos: Position, price: float, reason: str):
    pnl = unrealized_pnl_short(pos.entry, price, pos.qty)
    pnl_pct = (pos.entry - price) / pos.entry * 100.0
    win = pnl > 0

    # Execute buy-to-cover
    if not cfg.DRY_RUN:
        try:
            ex.create_market_order(pos.symbol, "buy", pos.qty)
        except Exception as e:
            tg.send(f"ERROR closing {pos.symbol}: {e}")
            return

    state.realized_pnl += pnl
    if win:
        state.wins += 1
    else:
        state.losses += 1

    msg = (
        f"{'PROFIT' if win else 'LOSS'} CLOSE\n"
        f"{pos.symbol}\n"
        f"reason={reason}\n"
        f"qty={pos.qty:.6f}\n"
        f"entry={pos.entry:.6f}\n"
        f"exit={price:.6f}\n"
        f"pnl={pnl:.2f} ({pnl_pct:.2f}%)\n"
        f"time={now_utc_iso()}"
    )
    if win:
        tg.send_profit(msg)
    else:
        tg.send_loss(msg)

    append_ledger({
        "ts": now_utc_iso(),
        "event": "CLOSE",
        "symbol": pos.symbol,
        "reason": reason,
        "qty": pos.qty,
        "entry": pos.entry,
        "exit": price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "win": win,
    })

def open_short(ex: Ex, cfg: Config, tg: Telegram, state: BotState, symbol: str, entry_price: float, atr_val: float):
    if len(state.positions) >= cfg.MAX_OPEN_TRADES:
        return

    equity = 0.0
    if not cfg.DRY_RUN:
        equity = get_equity_usdt(ex, cfg)
    else:
        # Paper equity approximation: assume starting equity in env or 1000
        equity = env_float("PAPER_EQUITY", 1000.0)

    usd = calc_position_usd(cfg, equity, entry_price)
    qty = usd_to_qty(ex, symbol, usd, entry_price)

    # Respect exchange min amount if known
    min_amt = ex.market_min_amount(symbol)
    if min_amt and qty < min_amt:
        qty = min_amt

    # Final sanity
    if usd < cfg.MIN_TRADE_USD or qty <= 0:
        return

    # Leverage set (best-effort)
    if not cfg.DRY_RUN:
        ex.set_leverage(symbol, cfg.LEVERAGE)

    # Place market sell to open short
    if not cfg.DRY_RUN:
        try:
            ex.create_market_order(symbol, "sell", qty)
        except Exception as e:
            tg.send(f"ERROR opening short {symbol}: {e}")
            return

    pos = build_position(cfg, symbol, qty, entry_price, time.time(), atr_val)
    state.positions[symbol] = pos

    msg = (
        f"OPEN SHORT\n"
        f"{symbol}\n"
        f"qty={qty:.6f}\n"
        f"entry={entry_price:.6f}\n"
        f"sl={pos.stop:.6f} ({cfg.STOP_LOSS_PCT:.2f}%)\n"
        f"tp={pos.tp:.6f} ({cfg.TAKE_PROFIT_PCT:.2f}%)\n"
        f"trail_start={cfg.TRAIL_START_PCT:.2f}% trail_dist={pos.trail_dist_pct:.2f}%\n"
        f"lev={cfg.LEVERAGE}x\n"
        f"time={now_utc_iso()}"
    )
    tg.send(msg)

    append_ledger({
        "ts": now_utc_iso(),
        "event": "OPEN",
        "symbol": symbol,
        "side": "short",
        "qty": qty,
        "entry": entry_price,
        "stop": pos.stop,
        "tp": pos.tp,
        "trail_dist_pct": pos.trail_dist_pct,
        "lev": cfg.LEVERAGE,
    })

# ----------------------------
# Reporting
# ----------------------------
def status_line(cfg: Config, state: BotState, ex: Optional[Ex] = None) -> str:
    w = state.wins
    l = state.losses
    winrate = (w / max(1, (w + l))) * 100.0
    open_trades = len(state.positions)
    return f"SHORT BOT | W/L {w}/{l} ({winrate:.1f}%) | Open {open_trades}/{cfg.MAX_OPEN_TRADES} | Realized {state.realized_pnl:.2f}"

def compute_open_pnl(ex: Ex, state: BotState) -> float:
    pnl = 0.0
    for sym, pos in state.positions.items():
        try:
            last = ex.fetch_ticker(sym).get("last") or 0.0
            pnl += unrealized_pnl_short(pos.entry, float(last), pos.qty)
        except Exception:
            pass
    return pnl

# ----------------------------
# Main loop
# ----------------------------
def main():
    cfg = Config()
    tg = Telegram()
    state = load_state()

    # Exchange optional in DRY_RUN. Still used for market data.
    ex = Ex(cfg)

    # Build symbols
    universe = build_universe(ex, cfg)
    if not universe:
        raise RuntimeError("No symbols in universe. Set SYMBOLS or check exchange markets.")

    tg.send(f"SHORT BOT START\nsymbols={len(universe)} tf={cfg.TF} dry_run={int(cfg.DRY_RUN)}\n{now_utc_iso()}")

    last_status = 0.0

    while True:
        try:
            # Guard NEW entries
            can_enter, guard_msg = market_guard(ex, cfg)

            # Manage existing positions first
            for sym, pos in list(state.positions.items()):
                try:
                    last = ex.fetch_ticker(sym).get("last") or 0.0
                    if last:
                        manage_position(ex, cfg, tg, state, pos, float(last))
                except Exception:
                    pass

            # Entry scan
            if can_enter and len(state.positions) < cfg.MAX_OPEN_TRADES:
                for sym in universe:
                    if len(state.positions) >= cfg.MAX_OPEN_TRADES:
                        break

                    # cooldown
                    cd_until = float(state.cooldown_until.get(sym, 0.0) or 0.0)
                    if time.time() < cd_until:
                        continue
                    if sym in state.positions:
                        continue

                    try:
                        ohlcv = ex.fetch_ohlcv(sym, cfg.TF, cfg.LOOKBACK_BARS)
                        ok, m = short_entry_signal(ohlcv, cfg)
                        if not ok:
                            continue

                        highs = [c[2] for c in ohlcv]
                        lows  = [c[3] for c in ohlcv]
                        closes= [c[4] for c in ohlcv]
                        atr_val = atr(highs, lows, closes, 14)

                        entry_price = closes[-1]
                        open_short(ex, cfg, tg, state, sym, float(entry_price), float(atr_val))

                        # Small delay to avoid burst orders
                        time.sleep(0.25)

                    except Exception:
                        continue

            # Status
            if time.time() - last_status >= cfg.STATUS_INTERVAL_SEC:
                open_pnl = compute_open_pnl(ex, state)
                tg.send(f"{status_line(cfg, state)} | OpenPnL {open_pnl:.2f} | {guard_msg}")
                last_status = time.time()
                save_state(state)

            time.sleep(1)

        except KeyboardInterrupt:
            tg.send("SHORT BOT STOP (keyboard)")
            save_state(state)
            return
        except Exception as e:
            tg.send(f"BOT ERROR: {e}")
            save_state(state)
            time.sleep(5)

if __name__ == "__main__":
    main()
