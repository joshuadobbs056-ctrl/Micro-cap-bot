# Coin Sniper — Savage ELITE (PAPER) — FLASH CRASH HUNTER (FULL INTEGRATED)
# 🎯 Goal: Catch 10%+ "Flash Crashes" and sell the bounce.
# ✅ API Connection Verification in Status Reports
# ✅ Full Wins/Losses/PnL Tracking
# ✅ 2-Second High-Speed Scan

import os, time, json, requests, traceback
import numpy as np
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple

# =========================
# CONFIG
# =========================
START_BALANCE = float(os.getenv("START_BALANCE", "2000"))
SCAN_INTERVAL = 2  
STATUS_INTERVAL = 60

# Entry Triggers
CRASH_THRESHOLD_PCT = 8.0     
VOL_SPIKE_RATIO = 4.0         
RSI_BUY_LEVEL = 15            

# Exit Triggers
RECOVERY_TARGET_PCT = 3.5     
STOP_LOSS_PCT = 5.0           
MAX_OPEN_TRADES = 5           

STATE_FILE = "coin_sniper_state.json"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
COINBASE_API = "https://api.exchange.coinbase.com"

@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_time: float
    high_water: float
    stop_price: float

# =========================
# HELPERS & NOTIFY
# =========================
def notify(msg: str):
    print(f"[LOG] {msg}", flush=True)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        except: pass

def check_api_connection() -> bool:
    try:
        r = requests.get(f"{COINBASE_API}/products/BTC-USD/ticker", timeout=5)
        return r.status_code == 200
    except: return False

def get_candles(product_id: str):
    try:
        r = requests.get(f"{COINBASE_API}/products/{product_id}/candles", params={"granularity": 60}, timeout=5)
        return r.json()[:60] if r.status_code == 200 else []
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

# =========================
# CORE LOGIC
# =========================
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
            return True, f"📉 CRASH! Drop:{drop_pct:.1f}% RSI:{rsi:.1f}"
    return False, ""

def status_report(state, positions, last_prices, api_ok):
    conn_icon = "🟢" if api_ok else "🔴"
    cash = float(state.get("cash", START_BALANCE))
    realized = float(state.get("realized_pnl", 0.0))
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    
    equity = cash + sum(last_prices.get(s, p.entry_price) * p.qty for s, p in positions.items())
    
    report = (
        f"📊 FLASH HUNTER REPORT\n"
        f"API Status: {conn_icon} Connected\n"
        f"Equity: ${equity:.2f} | Cash: ${cash:.2f}\n"
        f"Realized PnL: ${realized:.2f}\n"
        f"W/L: {wins}/{losses} | Open: {len(positions)}/{MAX_OPEN_TRADES}"
    )
    notify(report)

# =========================
# MAIN LOOP
# =========================
def main():
    state = {"cash": START_BALANCE, "wins": 0, "losses": 0, "realized_pnl": 0.0}
    positions: Dict[str, Position] = {}
    last_status_time = 0
    
    notify("🌪 FLASH HUNTER INITIALIZING...")
    
    try:
        products = requests.get(f"{COINBASE_API}/products").json()
        universe = [p['id'] for p in products if p['quote_currency'] == 'USD' and p['status'] == 'online']
    except: universe = []

    while True:
        try:
            api_ok = check_api_connection()
            current_prices = {}

            # 1. Manage Active Trades
            for sym, pos in list(positions.items()):
                candles = get_candles(sym)
                if not candles: continue
                px = float(candles[0][4])
                current_prices[sym] = px
                
                pnl_pct = (px / pos.entry_price - 1) * 100
                if pnl_pct >= RECOVERY_TARGET_PCT:
                    pnl_usd = (px - pos.entry_price) * pos.qty
                    state['cash'] += (px * pos.qty)
                    state['realized_pnl'] += pnl_usd
                    state['wins'] += 1
                    positions.pop(sym)
                    notify(f"💰 PROFIT EXIT: {sym} (+{pnl_pct:.2f}%) | +${pnl_usd:.2f}")
                elif pnl_pct <= -STOP_LOSS_PCT:
                    pnl_usd = (px - pos.entry_price) * pos.qty
                    state['cash'] += (px * pos.qty)
                    state['realized_pnl'] += pnl_usd
                    state['losses'] += 1
                    positions.pop(sym)
                    notify(f"❌ STOP LOSS: {sym} ({pnl_pct:.2f}%) | -${abs(pnl_usd):.2f}")

            # 2. Scan for New Entries
            if api_ok and len(positions) < MAX_OPEN_TRADES:
                for sym in universe:
                    if sym in positions: continue
                    candles = get_candles(sym)
                    is_crash, reason = detect_flash_crash(sym, candles)
                    
                    if is_crash:
                        px = float(candles[0][4])
                        buy_amt = state['cash'] / (MAX_OPEN_TRADES - len(positions))
                        if buy_amt > 10:
                            qty = buy_amt / px
                            positions[sym] = Position(sym, qty, px, time.time(), px, px*(1-STOP_LOSS_PCT/100))
                            state['cash'] -= buy_amt
                            notify(f"🚨 SNIPED {sym} @ ${px:.4f} | {reason}")

            # 3. Status Update
            if (time.time() - last_status_time) >= STATUS_INTERVAL:
                status_report(state, positions, current_prices, api_ok)
                last_status_time = time.time()

            time.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
