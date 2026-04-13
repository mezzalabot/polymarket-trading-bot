#!/usr/bin/env python3
"""
Paper Trading Module for Polymarket Assistant Tool
Auto-executes trades based on trend signals
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any

# Paper trading settings - SIMULATING LIVE TRADING PLAN
STARTING_BALANCE = 25.0     # $25 USDC virtual (modal awal minim)
TRADE_SIZE_USDC = 5.0       # $5 per trade (kecil-kecil)
MAX_TRADES_PER_DAY = 100    # Unlimited trades (effectively no limit)

# POLYMARKET FEE STRUCTURE
TAKER_FEE_PCT = 0.5         # 0.5% taker fee (market order)
MAKER_FEE_PCT = 0.0         # 0% maker fee (limit order)

# ORDER TYPE - HYBRID STRATEGY
# Entry: Market order (instant fill, 0.5% fee)
# Exit: Limit order (better price, 0% fee)

ENTRY_ORDER_TYPE = "market"   # Market order for entry (sure fill)
EXIT_ORDER_TYPE = "limit"     # Limit order for exit (better price, no fee)

ENTRY_FEE_PCT = 0.5           # 0.5% taker fee on entry
EXIT_FEE_PCT = 0.0            # 0% maker fee on exit (limit order)

# PRICE FILTERS - UNIVERSAL MIN 0.30 ENTRY
# Min entry price: 0.30 for ALL positions (UP and DOWN)
# Higher score = looser MAX limit, but MIN is always 0.30

def get_price_limits(score: float) -> tuple[float, float, float, float]:
    """
    Return (max_up_price, min_down_price, min_entry_price, max_down_price) based on confidence score.
    
    MIN ENTRY: 0.30 for all positions (UP and DOWN)
    MAX DOWN: 0.70 for all DOWN positions (ensure good RR)
    
    Score 90-100: UP max 0.75, DOWN min 0.30, DOWN max 0.70 (super yakin)
    Score 80-89:  UP max 0.65, DOWN min 0.30, DOWN max 0.70 (yakin)
    Score 75-79:  UP max 0.60, DOWN min 0.30, DOWN max 0.70 (hati-hati)
    """
    MIN_ENTRY = 0.30  # Universal minimum entry price
    MAX_DOWN = 0.70   # Maximum entry price for DOWN (min 30% upside)
    
    if score >= 90:
        return 0.75, 0.30, MIN_ENTRY, MAX_DOWN  # Longgar, percaya indikator
    elif score >= 80:
        return 0.65, 0.30, MIN_ENTRY, MAX_DOWN  # Standar
    else:  # 75-79
        return 0.60, 0.30, MIN_ENTRY, MAX_DOWN  # Ketat, hati-hati

# Default limits
MAX_ENTRY_PRICE_UP = 0.65     # Default max for UP
MIN_ENTRY_PRICE_DOWN = 0.15   # Default min for DOWN

# DEBUG LOGGING
DEBUG_MODE = True  # Enable verbose logging for troubleshooting
MIN_SCORE_THRESHOLD = 75    # Min trend score (konservatif, sangat yakin)
MAX_SCORE_THRESHOLD = 25    # Max score (konservatif, sangat yakin bearish)

# STOP LOSS & TAKE PROFIT SETTINGS - ADAPTIVE BASED ON CONFIDENCE
# Higher score = higher confidence = looser SL (percaya diri)
# Borderline score = lower confidence = tighter SL (hati-hati)

def get_sl_tp_pct(score: float, side: str) -> tuple[float, float]:
    """
    Return (SL%, TP%) based on confidence score.
    
    Score 90-100: SL 25%, TP 50% (RR 1:2) - Super yakin
    Score 80-89:  SL 20%, TP 40% (RR 1:2) - Yakin
    Score 75-79:  SL 15%, TP 30% (RR 1:2) - Borderline (hati-hati)
    """
    if score >= 90:
        return 25.0, 50.0  # Longgar, super yakin
    elif score >= 80:
        return 20.0, 40.0  # Standar
    else:  # 75-79
        return 15.0, 30.0  # Tight, hati-hati

# Default values for non-trade calculations
DEFAULT_SL_PCT = 20.0
DEFAULT_TP_PCT = 40.0

class PaperTrader:
    """Paper trading engine for trend-following strategy."""
    
    def __init__(self, data_dir: str = "paper_data"):
        self.data_dir = data_dir
        self.balance = STARTING_BALANCE
        self.positions = {}  # symbol: {side, entry_price, size, pnl}
        self.trade_history = []
        self.total_trades = 0
        self.total_pnl = 0.0
        self.trades_today = 0
        self.last_trade_date = datetime.now().strftime("%Y-%m-%d")
        self.last_entry_round = None  # Track 1 entry per round
        
        os.makedirs(data_dir, exist_ok=True)
        self._load_state()
        self._check_new_day()
    
    def _check_new_day(self):
        """Reset daily counter if new day."""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.last_trade_date:
            self.trades_today = 0
            self.last_trade_date = today
            self._save_state()
    
    def _load_state(self):
        """Load previous state if exists."""
        state_file = os.path.join(self.data_dir, "paper_state.json")
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                state = json.load(f)
                self.balance = state.get('balance', STARTING_BALANCE)
                self.positions = state.get('positions', {})
                self.total_trades = state.get('total_trades', 0)
                self.total_pnl = state.get('total_pnl', 0.0)
                self.trades_today = state.get('trades_today', 0)
                self.last_trade_date = state.get('last_trade_date', datetime.now().strftime("%Y-%m-%d"))
                self.trade_history = state.get('trade_history', [])  # Load trade history
                self.last_entry_round = state.get('last_entry_round', None)
                print(f"[Paper STATE LOADED] Balance: ${self.balance:.2f} | Last Round: {self.last_entry_round}", flush=True)
    
    def _save_state(self):
        """Save current state including trade history."""
        state_file = os.path.join(self.data_dir, "paper_state.json")
        state = {
            'balance': self.balance,
            'positions': self.positions,
            'total_trades': self.total_trades,
            'total_pnl': self.total_pnl,
            'trades_today': self.trades_today,
            'last_trade_date': self.last_trade_date,
            'last_updated': datetime.now().isoformat(),
            'trade_history': self.trade_history[-10:],  # Keep last 10 trades
            'last_entry_round': getattr(self, 'last_entry_round', None)
        }
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
        print(f"[Paper STATE SAVED] Balance: ${self.balance:.2f} | Trades: {self.trades_today} | Last Round: {getattr(self, 'last_entry_round', None)}", flush=True)
    
    async def check_sl_tp(self, symbol: str, pm_up_price: float = 0.0, pm_down_price: float = 0.0) -> Optional[Dict[str, Any]]:
        """
        Check if position hit Stop Loss or Take Profit.
        Uses adaptive SL/TP based on entry score.
        
        For UP position: 
            SL = entry_price - (entry_price * SL%)
            TP = entry_price + (entry_price * TP%)
        For DOWN position: 
            SL = entry_price + (entry_price * SL%)
            TP = entry_price - (entry_price * TP%)
        
        Returns trade dict if SL/TP hit, None otherwise.
        """
        if symbol not in self.positions:
            return None
        
        pos = self.positions[symbol]
        side = pos['side']
        entry = pos['entry_price']
        entry_score = pos.get('entry_score', 75)
        
        # Get adaptive SL/TP based on entry score
        sl_pct, tp_pct = get_sl_tp_pct(entry_score, side)
        
        # Calculate SL and TP distances
        sl_distance = entry * (sl_pct / 100)
        tp_distance = entry * (tp_pct / 100)
        
        if side == 'UP':
            sl_price = entry - sl_distance
            tp_price = entry + tp_distance
            current_price = pm_up_price
            
            # Debug logging
            print(f"[DEBUG SL/TP] UP position: entry={entry:.4f}, SL={sl_price:.4f}, TP={tp_price:.4f}, current={current_price:.4f}", flush=True)
            
            # Check TP first (take profit)
            if current_price >= tp_price:
                print(f"[DEBUG SL/TP] UP TAKE_PROFIT hit! {current_price:.4f} >= {tp_price:.4f}", flush=True)
                return self._close_position_sl(symbol, current_price, 'TAKE_PROFIT', sl_pct, tp_pct)
            # Check SL (stop loss)
            if current_price <= sl_price:
                print(f"[DEBUG SL/TP] UP STOP_LOSS hit! {current_price:.4f} <= {sl_price:.4f}", flush=True)
                return self._close_position_sl(symbol, current_price, 'STOP_LOSS', sl_pct, tp_pct)
                
        else:  # DOWN
            sl_price = entry + sl_distance
            tp_price = entry - tp_distance
            current_price = pm_down_price
            
            # Debug logging
            print(f"[DEBUG SL/TP] DOWN position: entry={entry:.4f}, SL={sl_price:.4f}, TP={tp_price:.4f}, current={current_price:.4f}", flush=True)
            
            # Check TP first (take profit for DOWN = price goes down)
            if current_price <= tp_price:
                print(f"[DEBUG SL/TP] DOWN TAKE_PROFIT hit! {current_price:.4f} <= {tp_price:.4f}", flush=True)
                return self._close_position_sl(symbol, current_price, 'TAKE_PROFIT', sl_pct, tp_pct)
            # Check SL (stop loss for DOWN = price goes up)
            if current_price >= sl_price:
                print(f"[DEBUG SL/TP] DOWN STOP_LOSS hit! {current_price:.4f} >= {sl_price:.4f}", flush=True)
                return self._close_position_sl(symbol, current_price, 'STOP_LOSS', sl_pct, tp_pct)
        
        return None
    
    def _close_position_sl(self, symbol: str, current_price: float, reason: str,
                           sl_pct: float = DEFAULT_SL_PCT, tp_pct: float = DEFAULT_TP_PCT) -> Dict[str, Any]:
        """Close position with specified reason (SL or signal reversal)."""
        if symbol not in self.positions:
            return None
        
        pos = self.positions[symbol]
        side = pos['side']
        entry_price = pos['entry_price']
        size = pos['size']
        entry_score = pos.get('entry_score', 75)
        entry_cost = pos.get('cost', size * entry_price)  # Total cost including entry fee
        
        # Calculate gross exit value (before fee)
        gross_exit_value = size * current_price
        
        # Apply exit fee (limit order = 0%)
        fee_pct = EXIT_FEE_PCT
        exit_fee = gross_exit_value * (fee_pct / 100)
        
        # Net exit value after fee
        exit_value = gross_exit_value - exit_fee
        
        # Calculate PnL: exit_value - entry_cost
        # Same for both UP and DOWN - profit if sell price > buy price
        pnl = exit_value - entry_cost
        
        # Debug logging
        print(f"[DEBUG CLOSE] {symbol} {side} @ {entry_price:.4f} -> {current_price:.4f}", flush=True)
        print(f"[DEBUG CLOSE] Entry cost: ${entry_cost:.4f}, Exit value: ${exit_value:.4f}", flush=True)
        print(f"[DEBUG CLOSE] PnL: ${pnl:.4f}, Reason: {reason}", flush=True)
        
        self.total_pnl += pnl
        self.balance += exit_value
        
        trade = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'side': side,
            'action': 'CLOSE',
            'reason': reason,
            'entry_price': entry_price,
            'exit_price': current_price,
            'size': size,
            'pnl': pnl,
            'exit_fee': exit_fee,
            'exit_fee_pct': fee_pct,
            'balance_after': self.balance,
            'entry_score': entry_score,
            'sl_pct': sl_pct,
            'tp_pct': tp_pct
        }
        self.trade_history.append(trade)
        
        del self.positions[symbol]
        self._save_state()
        
        return trade
    
    def check_signal(self, symbol: str, trend_score: float, pm_up_price: float, 
                     pm_down_price: float, candle_round: str = None) -> Optional[Dict[str, Any]]:
        """
        Check trend signal and execute paper trade if conditions met.
        Respects daily trade limit (max 4 per day).
        Also checks stop loss first.
        
        Returns trade dict if executed, None otherwise.
        """
        # Validate Polymarket prices - must be between 0 and 1 (exclusive)
        up_valid = 0 < pm_up_price < 1
        down_valid = 0 < pm_down_price < 1
        
        if not up_valid or not down_valid:
            print(f"[SKIP] Invalid Polymarket prices (UP: {pm_up_price}, DOWN: {pm_down_price}) - waiting for real data", flush=True)
            return None
        
        # CHECK CONTRADICTION between signal and price
        # Bearish signal + expensive DOWN = contradiction (skip)
        # Bullish signal + expensive UP = contradiction (skip)
        if trend_score <= MAX_SCORE_THRESHOLD:  # Bearish signal
            if pm_down_price > 0.70:  # DOWN too expensive = market thinks UP will win
                print(f"[SKIP CONTRADICTION] Bearish signal but DOWN price {pm_down_price:.3f} too high (market bullish)", flush=True)
                return None
        elif trend_score >= MIN_SCORE_THRESHOLD:  # Bullish signal
            if pm_up_price > 0.70:  # UP too expensive = market already priced in
                print(f"[SKIP CONTRADICTION] Bullish signal but UP price {pm_up_price:.3f} too high (low upside)", flush=True)
                return None
        
        # CHECK CONTRADICTION between signal and price
        # Bearish signal + expensive DOWN = contradiction (skip)
        # Bullish signal + expensive UP = contradiction (skip)
        if trend_score <= MAX_SCORE_THRESHOLD:  # Bearish signal
            if pm_down_price > 0.70:  # DOWN too expensive = market thinks UP will win
                print(f"[SKIP CONTRADICTION] Bearish signal but DOWN price {pm_down_price:.3f} too high (market bullish)", flush=True)
                return None
        elif trend_score >= MIN_SCORE_THRESHOLD:  # Bullish signal
            if pm_up_price > 0.70:  # UP too expensive = market already priced in
                print(f"[SKIP CONTRADICTION] Bullish signal but UP price {pm_up_price:.3f} too high (low upside)", flush=True)
                return None
        
        # Check if new day and reset counter
        self._check_new_day()
        
        # Check if already entered this round (1 entry per round max)
        current_last_round = getattr(self, 'last_entry_round', None)
        print(f"[DEBUG Paper] last_entry_round={current_last_round}, candle_round={candle_round}", flush=True)
        if candle_round and current_last_round == candle_round:
            print(f"[SKIP] Already entered round {candle_round} - Blocking new entry!", flush=True)
            return None
        
        # FIRST: Check SL/TP for existing position
        sltp_trade = self.check_sl_tp(symbol, pm_up_price, pm_down_price)
        if sltp_trade:
            return sltp_trade  # Position closed due to SL or TP
        
        # Check if already have position for this symbol (prevent double entry)
        if symbol in self.positions:
            print(f"[SKIP] Already have {self.positions[symbol]['side']} position for {symbol}", flush=True)
            return None
        
        # Check daily trade limit
        if self.trades_today >= MAX_TRADES_PER_DAY:
            return None  # Max 100 trades per day reached
        
        # Strong bullish signal - BUY UP token (with MIN 0.30 entry)
        if trend_score >= MIN_SCORE_THRESHOLD:
            # Get dynamic price limits + min entry
            max_up, _, min_entry, _ = get_price_limits(trend_score)
            
            # Check min entry price first (0.30 universal)
            if pm_up_price < min_entry:
                print(f"[SKIP] UP price {pm_up_price:.3f} < min entry {min_entry} (too cheap, low confidence)", flush=True)
            # Check max limit for this confidence level
            elif pm_up_price <= max_up:
                if symbol not in self.positions or self.positions.get(symbol, {}).get('side') != 'UP':
                    self.last_entry_round = candle_round  # Track entry round
                    return self._execute_trade(symbol, 'UP', pm_up_price, trend_score)
            else:
                conf = "SUPER YAKIN" if trend_score >= 90 else "YAKIN" if trend_score >= 80 else "BORDERLINE"
                print(f"[SKIP {conf}] UP price {pm_up_price:.3f} > limit {max_up} (too high for score {trend_score})", flush=True)
        
        # Strong bearish signal - BUY DOWN token (with MIN 0.30 entry, MAX 0.70)
        elif trend_score <= MAX_SCORE_THRESHOLD:
            # Get dynamic price limits + min/max entry
            bearish_score = 100 - trend_score  # Convert to bullish equivalent
            _, min_down, min_entry, max_down = get_price_limits(bearish_score)
            
            # Check min entry price first (0.30 universal)
            if pm_down_price < min_entry:
                print(f"[SKIP] DOWN price {pm_down_price:.3f} < min entry {min_entry} (too cheap, low confidence)", flush=True)
            # Check max entry price (0.70 universal - ensure min 30% upside)
            elif pm_down_price > max_down:
                print(f"[SKIP] DOWN price {pm_down_price:.3f} > max entry {max_down} (low upside, bad RR)", flush=True)
            # Check min limit for this confidence level
            elif pm_down_price >= min_down:
                if symbol not in self.positions or self.positions.get(symbol, {}).get('side') != 'DOWN':
                    self.last_entry_round = candle_round  # Track entry round
                    return self._execute_trade(symbol, 'DOWN', pm_down_price, trend_score)
            else:
                conf = "SUPER YAKIN" if trend_score <= 10 else "YAKIN" if trend_score <= 20 else "BORDERLINE"
                print(f"[SKIP {conf}] DOWN price {pm_down_price:.3f} < limit {min_down} (too low for score {trend_score})", flush=True)
        
        return None
    
    def _execute_trade(self, symbol: str, side: str, price: float, 
                       score: float) -> Dict[str, Any]:
        """Execute paper trade."""
        # Close existing position if any
        if symbol in self.positions:
            self._close_position(symbol, price)
        
        # Calculate gross size (before fee)
        gross_size = TRADE_SIZE_USDC / price if price > 0 else 0
        
        # Apply entry fee (market order = 0.5%)
        fee_pct = ENTRY_FEE_PCT
        fee_amount = (gross_size * price) * (fee_pct / 100)
        
        # Net size after fee
        size = gross_size * (1 - fee_pct / 100)
        
        # Total cost including fee
        cost = (gross_size * price)
        self.balance -= cost
        
        # Store fee info
        fee_info = {
            'fee_pct': fee_pct,
            'fee_amount': fee_amount,
            'gross_size': gross_size
        }
        
        # Record position with cost tracking for accurate PnL
        self.positions[symbol] = {
            'side': side,
            'entry_price': price,
            'size': size,
            'entry_time': datetime.now().isoformat(),
            'entry_score': score,
            'cost': cost  # Track total cost including entry fee
        }
        
        self.total_trades += 1
        self.trades_today += 1  # Increment daily counter
        
        trade = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'side': side,
            'action': 'OPEN',
            'price': price,
            'size': size,
            'gross_size': gross_size,
            'cost': cost,
            'entry_fee': fee_amount,
            'entry_fee_pct': fee_pct,
            'score': score,
            'balance_after': self.balance
        }
        self.trade_history.append(trade)
        self._save_state()
        
        return trade
    
    def _close_position(self, symbol: str, current_price: float):
        """Close existing position and calculate PnL."""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        side = pos['side']
        entry_price = pos['entry_price']
        size = pos['size']
        
        # Calculate PnL
        if side == 'UP':
            pnl = (current_price - entry_price) * size
        else:  # DOWN
            pnl = (entry_price - current_price) * size
        
        self.total_pnl += pnl
        exit_value = size * current_price
        self.balance += exit_value
        
        trade = {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'side': side,
            'action': 'CLOSE',
            'entry_price': entry_price,
            'exit_price': current_price,
            'size': size,
            'pnl': pnl,
            'balance_after': self.balance
        }
        self.trade_history.append(trade)
        
        del self.positions[symbol]
    
    def get_status(self) -> Dict[str, Any]:
        """Get current trading status."""
        # Get current price limits based on hypothetical scores
        limit_90_up, limit_90_down, _, max_down = get_price_limits(95)
        limit_80_up, limit_80_down, _, _ = get_price_limits(85)
        limit_75_up, limit_75_down, _, _ = get_price_limits(77)
        
        return {
            'balance': self.balance,
            'positions': self.positions,
            'total_trades': self.total_trades,
            'total_pnl': self.total_pnl,
            'roi_pct': (self.total_pnl / STARTING_BALANCE) * 100,
            'trades_today': self.trades_today,
            'trades_remaining': MAX_TRADES_PER_DAY - self.trades_today,
            'daily_limit': MAX_TRADES_PER_DAY,
            'price_filters': {
                'flexible_mode': True,
                'score_90_up_max': limit_90_up,
                'score_90_down_min': limit_90_down,
                'score_80_up_max': limit_80_up,
                'score_80_down_min': limit_80_down,
                'score_75_up_max': limit_75_up,
                'score_75_down_min': limit_75_down
            }
        }
    
    def format_trade_alert(self, trade: Dict[str, Any]) -> str:
        """Format trade for Telegram notification."""
        emoji = "🟢" if trade['side'] == 'UP' else "🔴"
        
        # Determine action type
        if trade['action'] == 'OPEN':
            action_emoji = "📥 OPEN"
            header = f"{emoji} <b>PAPER TRADE - OPEN</b> {emoji}"
        elif trade.get('reason') == 'STOP_LOSS':
            action_emoji = "🛑 STOP LOSS"
            header = f"⚠️ <b>STOP LOSS TRIGGERED</b> ⚠️"
            emoji = "🛑"
        elif trade.get('reason') == 'TAKE_PROFIT':
            action_emoji = "🎯 TAKE PROFIT"
            header = f"🎯 <b>TAKE PROFIT HIT!</b> 🎯"
            emoji = "🎯"
        else:
            action_emoji = "📤 CLOSE"
            header = f"{emoji} <b>PAPER TRADE - CLOSE</b> {emoji}"
        
        # Calculate 15m candle time for OPEN trades
        candle_info = ""
        if trade['action'] == 'OPEN':
            from datetime import datetime, timezone, timedelta
            et_offset = timedelta(hours=-4)
            et_tz = timezone(et_offset)
            now_et = datetime.now(et_tz)
            
            current_minute = now_et.minute
            candle_start_min = (current_minute // 15) * 15
            candle_end_min = candle_start_min + 15
            
            candle_start = now_et.replace(minute=candle_start_min, second=0, microsecond=0)
            if candle_end_min >= 60:
                candle_end = candle_start + timedelta(hours=1)
                candle_end = candle_end.replace(minute=0)
            else:
                candle_end = candle_start.replace(minute=candle_end_min)
            
            start_str = candle_start.strftime('%I:%M').lstrip('0')
            end_str = candle_end.strftime('%I:%M').lstrip('0')
            ampm = candle_start.strftime('%p')
            date_str = now_et.strftime('%B %d')
            
            candle_info = f"\n📅 {date_str} - {start_str} - {end_str} {ampm} ET\n"
        
        text = f"""
{header}{candle_info}

📊 Symbol: <code>{trade['symbol']}</code>
{action_emoji}: <b>{trade['side']}</b> token
💵 Price: {trade['price']:.4f}
📏 Size: {trade['size']:.2f}
"""
        if 'cost' in trade:
            text += f"💸 Cost: ${trade['cost']:.2f}\n"
            
            # For OPEN trades, calculate and show adaptive SL/TP
            if trade['action'] == 'OPEN':
                entry = trade['price']
                score = trade.get('score', 75)
                side = trade['side']
                sl_pct, tp_pct = get_sl_tp_pct(score, side)
                
                # Calculate fee info (entry only)
                fee_pct = ENTRY_FEE_PCT
                fee_amount = (trade.get('gross_size', trade['size']) * entry) * (fee_pct / 100)
                
                if side == 'UP':
                    sl_price = entry * (1 - sl_pct/100)  # Lower than entry
                    tp_price = entry * (1 + tp_pct/100)  # Higher than entry
                    sl_label = f"(-{sl_pct:.0f}%)"
                    tp_label = f"(+{tp_pct:.0f}%)"
                else:  # DOWN - INVERTED!
                    # For DOWN: SL = price goes down (loss), TP = price goes up (win)
                    sl_price = entry * (1 - sl_pct/100)  # Lower = loss for DOWN
                    tp_price = entry * (1 + tp_pct/100)  # Higher = win for DOWN
                    sl_label = f"(↓{sl_pct:.0f}%)"  # Price down = SL for DOWN
                    tp_label = f"(↑{tp_pct:.0f}%)"  # Price up = TP for DOWN
                
                # Get price limits for this score
                max_up_limit, min_down_limit, _, max_down_limit = get_price_limits(score)
                price_limit = max_up_limit if side == 'UP' else min_down_limit
                
                # Show confidence level
                if score >= 90:
                    conf = "🔥 SUPER YAKIN"
                elif score >= 80:
                    conf = "✅ YAKIN"
                else:
                    conf = "⚠️ BORDERLINE"
                
                text += f"\n{conf} (Score: {score})\n"
                text += f"📊 Price Limit: {price_limit:.2f} (flexible)\n"
                text += f"💸 Entry Fee: ${fee_amount:.3f} ({fee_pct:.1f}%)\n"
                text += f"🛑 SL: {sl_price:.4f} {sl_label}\n"
                text += f"🎯 TP: {tp_price:.4f} {tp_label}\n"
                text += f"📊 RR: 1:{tp_pct/sl_pct:.1f}\n"
                
        if 'pnl' in trade:
            pnl_emoji = "🟢" if trade['pnl'] >= 0 else "🔴"
            text += f"{pnl_emoji} PnL: ${trade['pnl']:.2f}\n"
            
        # Show fee info for CLOSE trades
        if 'exit_fee' in trade and trade['exit_fee'] > 0:
            text += f"💸 Exit Fee: ${trade['exit_fee']:.3f} ({trade.get('exit_fee_pct', 0):.1f}%)\n"
        elif trade.get('action') == 'CLOSE':
            text += f"💸 Exit Fee: $0 (0% - Limit Order)\n"
        
        if 'entry_price' in trade and trade['action'] == 'CLOSE':
            text += f"📊 Entry: {trade['entry_price']:.4f} → Exit: {trade['exit_price']:.4f}\n"
        
        if 'score' in trade:
            text += f"📈 Trend Score: {trade['score']:.0f}/100\n"
        
        if 'reason' in trade:
            entry = trade['entry_price']
            side = trade['side']
            if trade['reason'] == 'STOP_LOSS':
                sl_pct = trade.get('sl_pct', DEFAULT_SL_PCT)
                sl_price = entry * (1 - sl_pct/100) if side == 'UP' else entry * (1 + sl_pct/100)
                text += f"🛑 SL: {sl_price:.4f} (-{sl_pct:.0f}%)\n"
            elif trade['reason'] == 'TAKE_PROFIT':
                tp_pct = trade.get('tp_pct', DEFAULT_TP_PCT)
                tp_price = entry * (1 + tp_pct/100) if side == 'UP' else entry * (1 - tp_pct/100)
                text += f"🎯 TP: {tp_price:.4f} (+{tp_pct:.0f}%)\n"
        
        text += f"💰 Balance: ${trade['balance_after']:.2f}\n"
        text += f"📅 Trades Today: {self.trades_today}/{MAX_TRADES_PER_DAY}\n"
        text += f"⏰ {trade['timestamp'][:19]}"
        
        return text
