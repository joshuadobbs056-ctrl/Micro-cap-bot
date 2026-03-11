# Coin Sniper — Savage ELITE (PAPER) — FLASH CRASH HUNTER (FULL VERSION)
# 🎯 Goal: Catch 10%+ "Flash Crashes" and sell the bounce.
# ✅ Full Telegram Updates (Wins, Losses, Realized PnL)
# ✅ Exact Status Formatting (W/L, Equity, Open trades)
# ✅ High-Speed Scan (2-second interval)
# ✅ Persistent State Saving

import os, time, json, csv, traceback
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple
import requests
import numpy as np

# =========================
# CONFIG (PRIMED FOR VOLATILITY)
# =========================
START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
SCAN_INTERVAL = 2  
STATUS_INTERVAL = 60

# --- THE "MASSIVE MOVEMENT" TRIGGERS ---
CRASH_THRESHOLD_PCT = 8.0     
VOL_SPIKE_RATIO = 4.0         
RSI_BUY_LEVEL = 15            
RECOVERY_TARGET_PCT = 3.5     

# --- RISK LIMITS ---
MAX_OPEN_TRADES = 5           
STOP_LOSS_PCT = 5.0           
MAX_SYMBOL_AGE_MINS = 30      

STATE_FILE = "coin_sniper_state.json"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
COINBASE_API = "https://api.exchange.coinbase.com"

# =========================
# MODELS & NOTIFICATIONS
# =========================
@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_time: float
    high_water: float
    stop_price: float
    is_flash_trade: bool = True

def notify(msg: str):
    print(msg, flush=True)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
        except: pass

# =========================
# MATH & API
# =========================
def get_candles(product_id: str, granularity: int = 60) -> List[list]:
    try:
        r = requests.get(f"{COINBASE_API}/products/{product_id}/candles", 
                         params={"granularity": granularity}, timeout=10)
        return r.json()[:60]
    except: return []

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = np.diff(prices)
    up = np.where(deltas > 0, deltas, 0)
    down = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(up[:period])
    avg_loss = np.mean(down[:period])
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def detect_flash_crash(sym: str, candles: List[list]) -> Tuple[bool, str]:
    if len(candles) < 20: return False, ""
    closes = [float(c[4]) for c in reversed(candles)]
    vols = [float(c[5]) for c in reversed(candles)]
    current_price = closes[-1]
    prev_price_5m = closes[-6] if len(closes) >= 6 else closes[0]
    drop_pct = ((prev_price_5m - current_price) / prev_price_5m) * 100
    avg_vol = np.mean(vols[-15:-1])
    rsi = calculate_rsi(closes)
    
    if drop_pct >= CRASH_THRESHOLD_PCT and vols[-1] > (avg_vol * VOL_SPIKE_RATIO):
        if rsi <= RSI_BUY_LEVEL:
            return True, f"CRASH! Drop:{drop_pct:.1f}% RSI:{rsi:.1f}"
    return False, ""

# =========================
# STATE MANAGEMENT
# =========================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f: return json.load(f)
        except: pass
    return {"cash": START_BALANCE, "wins": 0, "losses": 0, "realized_pnl": 0.0, "positions": {}}

def save_state(state, positions):
    state["positions"] = {k: asdict(v) for k, v in positions.items()}
    with open(STATE_FILE, "w") as f: json.dump(state, f, indent=2)

# =========================
# REPORTING
# =========================
def status_report(state, positions, last_prices):
    cash = float(state["cash"])
    wins = int(state.get("wins", 0))
    losses = int(state.get("losses", 0))
    realized = float(state.get("realized_pnl", 0.0))
    total_trades = wins + losses
    win_pct = (wins / total_trades * 100) if total_trades > 0 else 0.0
    
    equity = cash
    pos_lines = []
    for sym, pos in positions.items():
        px = last_prices.get(sym, pos.entry_price)
        equity += (px * pos.qty)
        pnl_p = (px / pos.entry_price - 1) * 100
        pos_lines.append(f" - {sym}: qty={pos.qty:.6f} entry={pos.entry_price:.6f} now={px:.6f} pnl%={pnl_p:.2f} high={pos.high_water:.6f}")

    report = (
        f"📊 Coin Sniper FLASH HUNTER\n"
        f"Cash: ${cash:.2f} | Equity: ${equity:.2f} | Realized PnL: ${realized:.2f}\n"
        f"W/L: {wins}/{losses} | Win%: {win_pct:.1f}% | Open: {len(positions)}/{MAX_OPEN_TRADES}\n"
        f"Open positions:\n" + ("\n".join(pos_lines) if pos_lines else "None")
    )
    notify(report)

# =========================
# CORE LOOP
# =========================
def main():
    state = load_state()
    positions = {k: Position(**v) for k, v in state.get("positions", {}).items()}
    last_status = 0
    
    products = requests.get(f"{COINBASE_API}/products").json()
    universe = [p['id'] for p in products if p['quote_currency'] == 'USD' and p['status'] == 'online']
    
    notify("🌪 FLASH HUNTER ONLINE. Scanning for massive movements...")

    while True:
        try:
            last_prices = {}
            for sym, pos in list(positions.items()):
                candles = get_candles(sym)
                if not candles: continue
                px = float(candles[0][4])
                last_prices[sym] = px
                
                pnl_pct = (px / pos.entry_price - 1) * 100
                if pnl_pct >= RECOVERY_TARGET_PCT:
                    exit_trade(state, positions, sym, px, f"BOUNCE ({pnl_pct:.2f}%)")
                elif pnl_pct <= -STOP_LOSS_PCT:
                    exit_trade(state, positions, sym, px, "STOP LOSS")

            if len(positions) < MAX_OPEN_TRADES:
                for sym in universe:
                    if sym in positions: continue
                    candles = get_candles(sym)
                    is_crash, reason = detect_flash_crash(sym, candles)
                    if is_crash:
                        px = float(candles[0][4])
                        buy_size = state['cash'] / (MAX_OPEN_TRADES - len(positions))
                        qty = buy_size / px
                        positions[sym] = Position(sym, qty, px, time.time(), px, px*(1-STOP_LOSS_PCT/100))
                        state['cash'] -= buy_size
                        notify(f"🚨 SNIPED {sym} @ {px:.6f} | {reason}")

            now = time.time()
            if (now - last_status) >= STATUS_INTERVAL:
                status_report(state, positions, last_prices)
                last_status = now
                save_state(state, positions)

            time.sleep(SCAN_INTERVAL)
        except Exception as e:
            time.sleep(5)

def exit_trade(state, positions, sym, px, reason):
    pos = positions.pop(sym)
    pnl = (px - pos.entry_price) * pos.qty
    state['cash'] += (pos.qty * px)
    state['realized_pnl'] += pnl
    if pnl > 0: state['wins'] += 1
    else: state['losses'] += 1
    notify(f"💰 EXIT {sym} @ {px:.6f} | PnL: ${pnl:.2f} | {reason}")

if __name__ == "__main__":
    main()
