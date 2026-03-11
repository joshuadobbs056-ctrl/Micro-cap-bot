# Coin Sniper — Savage ELITE (PAPER) — FLASH CRASH HUNTER
# 🎯 Goal: Catch 10%+ "Flash Crashes" and sell the immediate 2-5% bounce.
# 🛠 Strategy: High-speed RSI oversold + Volume Spike detection.

import os, time, json, csv, traceback
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple
import requests
import numpy as np

# =========================
# CONFIG (PRIMED FOR VOLATILITY)
# =========================
START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
SCAN_INTERVAL = 2  # Faster scanning for rapid movements
STATUS_INTERVAL = 60

# --- THE "MASSIVE MOVEMENT" TRIGGERS ---
CRASH_THRESHOLD_PCT = 8.0     # Minimum drop in 5 minutes to trigger a buy
VOL_SPIKE_RATIO = 4.0         # Volume must be 4x the recent average
RSI_BUY_LEVEL = 15            # Extreme oversold condition
RECOVERY_TARGET_PCT = 3.5     # Sell quickly on the bounce

# --- RISK LIMITS ---
MAX_OPEN_TRADES = 5           # Concentrating capital for "thousands" goal
STOP_LOSS_PCT = 5.0           # Exit if the "bounce" fails and it keeps dipping
MAX_SYMBOL_AGE_MINS = 30      # Don't hold "crash" coins for more than 30 mins

# =========================
# DATA MODELS & HELPERS
# =========================
@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_time: float
    high_water: float
    stop_price: float
    is_flash_trade: bool

def notify(msg: str):
    print(msg, flush=True)
    if os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        try:
            requests.post(f"https://api.telegram.org/bot{os.getenv('TELEGRAM_TOKEN')}/sendMessage",
                          json={"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "text": msg}, timeout=5)
        except: pass

COINBASE_API = "https://api.exchange.coinbase.com"

def get_candles(product_id: str, granularity: int = 60) -> List[list]:
    try:
        r = requests.get(f"{COINBASE_API}/products/{product_id}/candles", 
                         params={"granularity": granularity}, timeout=10)
        return r.json()[:60] # Last hour of data
    except: return []

# =========================
# MATH: RSI & CRASH DETECT
# =========================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)

    for i in range(period, len(prices)):
        delta = deltas[i - 1]
        if delta > 0:
            upval, downval = delta, 0.
        else:
            upval, downval = 0., -delta
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
    return rsi[-1]

def detect_flash_crash(sym: str, candles: List[list]) -> Tuple[bool, str]:
    """Checks for sudden massive price drops + volume confirmation."""
    if len(candles) < 10: return False, ""
    
    # [0]time, [1]low, [2]high, [3]open, [4]close, [5]volume
    closes = [float(c[4]) for c in reversed(candles)]
    vols = [float(c[5]) for c in reversed(candles)]
    
    current_price = closes[-1]
    prev_price_5m = closes[-6] if len(closes) >= 6 else closes[0]
    
    drop_pct = ((prev_price_5m - current_price) / prev_price_5m) * 100
    avg_vol = np.mean(vols[-15:-1])
    current_vol = vols[-1]
    
    rsi = calculate_rsi(closes)
    
    if drop_pct >= CRASH_THRESHOLD_PCT and current_vol > (avg_vol * VOL_SPIKE_RATIO):
        if rsi <= RSI_BUY_LEVEL:
            return True, f"CRASH! Drop:{drop_pct:.1f}% VolRatio:{current_vol/avg_vol:.1f} RSI:{rsi:.1f}"
    
    return False, ""

# =========================
# CORE EXECUTION
# =========================
def main():
    state = {"cash": START_BALANCE, "wins": 0, "losses": 0, "pnl": 0.0, "positions": {}}
    positions: Dict[str, Position] = {}
    
    # Get universe of USD pairs
    products = requests.get(f"{COINBASE_API}/products").json()
    universe = [p['id'] for p in products if p['quote_currency'] == 'USD' and p['status'] == 'online']
    
    notify("🌪 FLASH CRASH HUNTER ONLINE. Monitoring for massive movements...")

    while True:
        try:
            # 1. Manage Active Trades
            for sym, pos in list(positions.items()):
                candles = get_candles(sym)
                if not candles: continue
                px = float(candles[0][4])
                
                pnl_pct = (px / pos.entry_price - 1) * 100
                
                # Exit Logic: Take Profit or Stop Loss
                if pnl_pct >= RECOVERY_TARGET_PCT:
                    reason = f"BOUNCE REACHED ({pnl_pct:.2f}%)"
                    exit_trade(state, positions, sym, px, reason)
                elif pnl_pct <= -STOP_LOSS_PCT:
                    exit_trade(state, positions, sym, px, "STOP LOSS")
                elif (time.time() - pos.entry_time) > (MAX_SYMBOL_AGE_MINS * 60):
                    exit_trade(state, positions, sym, px, "TIME EXPIRED")

            # 2. Scan for New Crashes
            if len(positions) < MAX_OPEN_TRADES:
                for sym in universe:
                    if sym in positions: continue
                    
                    candles = get_candles(sym)
                    is_crash, reason = detect_flash_crash(sym, candles)
                    
                    if is_crash:
                        px = float(candles[0][4])
                        buy_size = state['cash'] / (MAX_OPEN_TRADES - len(positions))
                        qty = buy_size / px
                        
                        positions[sym] = Position(
                            symbol=sym, qty=qty, entry_price=px, entry_time=time.time(),
                            high_water=px, stop_price=px*(1-STOP_LOSS_PCT/100), is_flash_trade=True
                        )
                        state['cash'] -= buy_size
                        notify(f"🚨 SNIPED {sym} @ {px:.6f} | {reason}")

            time.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

def exit_trade(state, positions, sym, px, reason):
    pos = positions.pop(sym)
    trade_value = pos.qty * px
    pnl = trade_value - (pos.qty * pos.entry_price)
    state['cash'] += trade_value
    state['pnl'] += pnl
    if pnl > 0: state['wins'] += 1
    else: state['losses'] += 1
    
    notify(f"✅ EXIT {sym} @ {px:.6f} | PnL: ${pnl:.2f} | {reason}")

if __name__ == "__main__":
    main()
