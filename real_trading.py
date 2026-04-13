#!/usr/bin/env python3
"""
Real Trading Module - With correct price limits
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# Import Telegram notifier for SL/TP alerts
try:
    from telegram_notifier import send_sl_tp_alert
    _telegram_available = True
    print("[INFO] Telegram SL/TP notifier loaded successfully")
except ImportError as e:
    _telegram_available = False
    print(f"[WARNING] Telegram notifier not available for SL/TP alerts: {e}")
except Exception as e:
    _telegram_available = False
    print(f"[ERROR] Failed to load Telegram notifier: {e}")

_global_executor = None
_global_executor_initialized = False

STARTING_BALANCE = 25.0
TRADE_SIZE_USDC = 5.0
MAX_TRADES_PER_DAY = 100  # Unlimited trades

# Global lock untuk mencegah race condition
_trade_lock = asyncio.Lock()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Strategy switches (data-driven defaults from 2026-04-13 review)
ENABLE_UP_ENTRIES = _env_bool("ENABLE_UP_ENTRIES", True)
ENABLE_DOWN_ENTRIES = _env_bool("ENABLE_DOWN_ENTRIES", False)
UP_SCORE_MIN = _env_float("UP_SCORE_MIN", 75.0)
UP_SCORE_MAX = _env_float("UP_SCORE_MAX", 80.0)
DOWN_SCORE_MIN = _env_float("DOWN_SCORE_MIN", 0.0)
DOWN_SCORE_MAX = _env_float("DOWN_SCORE_MAX", 25.0)
ALLOW_NEUTRAL_TREND = _env_bool("ALLOW_NEUTRAL_TREND", False)
KEEP_POSITION_OPEN_ON_FAILED_LIVE_CLOSE = _env_bool("KEEP_POSITION_OPEN_ON_FAILED_LIVE_CLOSE", True)

# Entry quality filters for dry-run validation
REQUIRE_SIGNAL_CONFIRMATION = _env_bool("REQUIRE_SIGNAL_CONFIRMATION", True)
SIGNAL_CONFIRMATION_COUNT = max(1, _env_int("SIGNAL_CONFIRMATION_COUNT", 2))
ENABLE_CHOP_FILTER = _env_bool("ENABLE_CHOP_FILTER", True)
MIN_EMA_SPREAD_PCT = _env_float("MIN_EMA_SPREAD_PCT", 0.10)
MIN_ATR_PCT = _env_float("MIN_ATR_PCT", 0.12)
COOLDOWN_AFTER_SL_CANDLES = max(0, _env_int("COOLDOWN_AFTER_SL_CANDLES", 1))

def get_price_limits(score: float):
    """Static price limits for the current expectancy-optimized setup.

    Notes:
    - UP entries are capped at 0.55 to avoid chasing late bullish moves.
    - DOWN entries are disabled by default, but bounds stay available for future re-tests.
    """
    UP_MAX = 0.55      # Don't buy UP at resistance (prevent chasing)
    DOWN_MIN = 0.45    # Don't sell DOWN at support (prevent chasing)
    MIN_ENTRY = 0.30   # Universal minimum entry
    MAX_DOWN = 0.70    # Maximum entry for DOWN
    
    return UP_MAX, DOWN_MIN, MIN_ENTRY, MAX_DOWN

def get_entry_price_filter(side: str, price: float) -> tuple[bool, str]:
    """Validate entry price to prevent chasing.
    
    Returns: (is_valid, reason)
    """
    if side == "UP":
        if price > 0.55:
            return False, f"SKIP UP - Price {price:.4f} too high (chasing resistance, max 0.55)"
        if price < 0.30:
            return False, f"SKIP UP - Price {price:.4f} below min 0.30"
    elif side == "DOWN":
        if price < 0.45:
            return False, f"SKIP DOWN - Price {price:.4f} too low (chasing support, min 0.45)"
        if price > 0.70:
            return False, f"SKIP DOWN - Price {price:.4f} above max 0.70"
    return True, ""

def get_sl_tp_pct(score: float):
    """Flat SL/TP based on data analysis.
    
    Analysis showed adaptive SL (20-25% for high scores) caused more losses.
    Flat 15% SL / 30% TP optimal for 15m timeframe.
    """
    # Flat SL/TP for all scores - adaptive caused chasing issues
    return 15, 30  # SL 15%, TP 30%


def calculate_trend_direction(klines: list, lookback: int = 5) -> str:
    """Calculate higher timeframe trend direction from klines data.
    
    Uses EMA cross and price position to determine trend.
    Returns: "UPTREND", "DOWNTREND", or "NEUTRAL"
    
    Args:
        klines: List of candle data with 'c' (close) prices
        lookback: Number of candles to analyze (default 5)
    """
    if len(klines) < lookback + 5:
        return "NEUTRAL"  # Not enough data
    
    try:
        # Get recent closes
        recent_closes = [k['c'] for k in klines[-lookback:]]
        
        # Simple trend detection: compare recent closes
        first_price = recent_closes[0]
        last_price = recent_closes[-1]
        
        # Calculate slope/change
        price_change = ((last_price - first_price) / first_price) * 100
        
        # Determine trend with threshold
        if price_change > 0.5:  # >0.5% up in lookback period
            return "UPTREND"
        elif price_change < -0.5:  # >0.5% down in lookback period
            return "DOWNTREND"
        else:
            return "NEUTRAL"
            
    except Exception as e:
        print(f"[TREND CALC ERROR] {e}", flush=True)
        return "NEUTRAL"


def check_trend_filter(side: str, trend_direction: str, klines: list = None) -> tuple[bool, str]:
    """Check if trade aligns with higher timeframe trend.

    Strategy mode is strict by default:
    - UPTREND  -> only UP entries
    - DOWNTREND -> only DOWN entries
    - NEUTRAL -> blocked unless ALLOW_NEUTRAL_TREND is enabled
    """
    if trend_direction == "NEUTRAL":
        if ALLOW_NEUTRAL_TREND:
            return True, "Neutral trend allowed by config"
        return False, f"SKIP {side} - Neutral trend blocked (strict trend mode)"

    if trend_direction == "UPTREND":
        if side == "DOWN":
            return False, f"SKIP {side} - Counter-trend (market in uptrend)"
        return True, f"ALLOW {side} - With uptrend"

    if trend_direction == "DOWNTREND":
        if side == "UP":
            return False, f"SKIP {side} - Counter-trend (market in downtrend)"
        return True, f"ALLOW {side} - With downtrend"

    return False, f"SKIP {side} - Unknown trend state: {trend_direction}"

def _extract_closes(klines: list) -> list[float]:
    closes = []
    for candle in klines or []:
        try:
            closes.append(float(candle["c"]))
        except (KeyError, TypeError, ValueError):
            continue
    return closes


def _extract_hlc(klines: list) -> tuple[list[float], list[float], list[float]]:
    highs, lows, closes = [], [], []
    for candle in klines or []:
        try:
            highs.append(float(candle["h"]))
            lows.append(float(candle["l"]))
            closes.append(float(candle["c"]))
        except (KeyError, TypeError, ValueError):
            continue
    return highs, lows, closes


def _ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    ema_value = values[0]
    for value in values[1:]:
        ema_value = alpha * value + (1 - alpha) * ema_value
    return ema_value


def _atr_pct(klines: list, period: int = 14) -> Optional[float]:
    highs, lows, closes = _extract_hlc(klines)
    if len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    recent_tr = true_ranges[-period:]
    if not recent_tr:
        return None
    avg_tr = sum(recent_tr) / len(recent_tr)
    last_close = closes[-1]
    if not last_close:
        return None
    return (avg_tr / last_close) * 100


def get_market_filters(klines: list) -> dict[str, Any]:
    closes = _extract_closes(klines)
    result: dict[str, Any] = {
        "ema_spread_pct": None,
        "atr_pct": None,
        "filter_ok": True,
        "reason": "OK",
    }
    if len(closes) < 50:
        result["filter_ok"] = False
        result["reason"] = "Not enough klines for chop filter"
        return result

    ema20 = _ema(closes[-80:], 20)
    ema50 = _ema(closes[-120:], 50)
    atr_pct = _atr_pct(klines, period=14)
    last_close = closes[-1]

    ema_spread_pct = None
    if ema20 is not None and ema50 is not None and last_close:
        ema_spread_pct = abs(ema20 - ema50) / last_close * 100

    result["ema_spread_pct"] = ema_spread_pct
    result["atr_pct"] = atr_pct

    if not ENABLE_CHOP_FILTER:
        return result

    if ema_spread_pct is None or atr_pct is None:
        result["filter_ok"] = False
        result["reason"] = "Missing EMA/ATR metrics"
        return result

    if ema_spread_pct < MIN_EMA_SPREAD_PCT:
        result["filter_ok"] = False
        result["reason"] = f"EMA spread too tight ({ema_spread_pct:.3f}% < {MIN_EMA_SPREAD_PCT:.3f}%)"
        return result

    if atr_pct < MIN_ATR_PCT:
        result["filter_ok"] = False
        result["reason"] = f"ATR too low ({atr_pct:.3f}% < {MIN_ATR_PCT:.3f}%)"
        return result

    return result


class RealTrader:
    def __init__(self, data_dir: str = "~/polymarket-bot/real_data"):
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.state_file = os.path.join(self.data_dir, "real_state.json")
        
        self.private_key = os.getenv("PRIVATE_KEY")
        self.wallet_connected = bool(self.private_key)
        
        global _global_executor, _global_executor_initialized
        self.executor = _global_executor
        self.executor_ready = _global_executor_initialized
        
        self.balance = STARTING_BALANCE
        self.positions = {}
        self.total_trades = 0
        self.total_pnl = 0.0
        self.trades_today = 0
        self.last_trade_date = datetime.now().strftime("%Y-%m-%d")
        self.trade_history = []
        
        # Win/Loss tracking
        self.wins = 0
        self.losses = 0
        
        # Track entry round to prevent multiple entries in same candle
        self.last_entry_round: Optional[str] = None
        self.pending_signals: Dict[str, Dict[str, Any]] = {}
        self.cooldown_until_index: Dict[str, int] = {}
        self.last_stop_loss_reason: Dict[str, Any] = {}
        
        self._load_state()
        
        print(f"🚀 REAL TRADER INITIALIZED (v8 + TREND FILTER)")
        print(f"   💰 Balance: ${self.balance:.2f} (REAL WALLET)")
        print(f"   🔗 Wallet: 0xc668...1D5A")
        print(f"   ⚡ Real Execution: READY")
        print(f"   📈 Trend Filter: ACTIVE (1H trend detection)")
        print(f"   Trades Today: {self.trades_today}/4")
        print(f"   Price Limits: UP max 0.55, DOWN min 0.45")
    
    def _load_state(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                self.balance = state.get('balance', STARTING_BALANCE)
                self.trades_today = state.get('trades_today', 0)
                self.last_trade_date = state.get('last_trade_date', datetime.now().strftime("%Y-%m-%d"))
                self.executor_ready = state.get('executor_ready', False)
                self.total_trades = state.get('total_trades', 0)
                self.total_pnl = state.get('total_pnl', 0.0)
                self.wins = state.get('wins', 0)
                self.losses = state.get('losses', 0)
                self.positions = state.get('positions', {})  # Load positions
                self.last_entry_round = state.get('last_entry_round', None)
                self.trade_history = state.get('trade_history', [])  # Load trade history
                self.pending_signals = state.get('pending_signals', {})
                self.cooldown_until_index = state.get('cooldown_until_index', {})
                self.last_stop_loss_reason = state.get('last_stop_loss_reason', {})
                if self.positions:
                    print(f"[STATE LOADED] Loaded {len(self.positions)} position(s): {list(self.positions.keys())}", flush=True)
    
    def _save_state(self):
        state = {
            'balance': self.balance,
            'trades_today': self.trades_today,
            'last_trade_date': self.last_trade_date,
            'last_updated': datetime.now().isoformat(),
            'executor_ready': self.executor_ready,
            'total_trades': self.total_trades,
            'total_pnl': self.total_pnl,
            'wins': self.wins,
            'losses': self.losses,
            'positions': self.positions,  # Save positions to persist
            'last_entry_round': self.last_entry_round,
            'trade_history': self.trade_history,  # Save trade history
            'pending_signals': self.pending_signals,
            'cooldown_until_index': self.cooldown_until_index,
            'last_stop_loss_reason': self.last_stop_loss_reason,
        }
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
        print(f"[STATE SAVED] Balance: ${self.balance:.2f} | Trades: {self.trades_today} | Positions: {len(self.positions)} | Last Round: {self.last_entry_round}", flush=True)
    
    def _get_current_trend(self, symbol: str) -> str:
        """Get current 1H trend direction for symbol.
        
        Uses Binance 1H klines to determine trend.
        Falls back to NEUTRAL if data unavailable.
        
        Returns: "UPTREND", "DOWNTREND", or "NEUTRAL"
        """
        try:
            # Import here to avoid circular dependency
            from src import feeds
            
            # Get 1H klines from feeds module (if available)
            # For now, use simple heuristic based on recent price action
            # This will be enhanced when we have proper 1H data feed
            
            # Try to get from feeds state if available
            if hasattr(feeds, '_state') and feeds._state:
                klines = feeds._state.klines
                if klines and len(klines) >= 10:
                    return calculate_trend_direction(klines, lookback=10)
            
            # Fallback: Check if we have position to infer trend
            if symbol in self.positions:
                # If we have UP position and it's still open, assume uptrend
                # If we have DOWN position and it's still open, assume downtrend
                pos = self.positions[symbol]
                if pos['side'] == 'UP':
                    return "UPTREND"
                elif pos['side'] == 'DOWN':
                    return "DOWNTREND"
            
            # Default to neutral if no data
            return "NEUTRAL"
            
        except Exception as e:
            print(f"[TREND GET ERROR] {e}", flush=True)
            return "NEUTRAL"
    
    def _is_in_cooldown(self, symbol: str, candle_index: Optional[int]) -> tuple[bool, str]:
        if candle_index is None:
            return False, ""
        cooldown_until = self.cooldown_until_index.get(symbol)
        if cooldown_until is None:
            return False, ""
        if candle_index < cooldown_until:
            remaining = cooldown_until - candle_index
            return True, f"Cooldown active after SL ({remaining} candle(s) remaining)"
        return False, ""

    def _register_signal_confirmation(
        self,
        symbol: str,
        side: str,
        candle_index: Optional[int],
        score: float,
        price: float,
        trend_direction: str,
    ) -> tuple[bool, str]:
        if not REQUIRE_SIGNAL_CONFIRMATION:
            return True, "Signal confirmation disabled"

        bucket = self.pending_signals.get(symbol, {})
        signal_key = f"{side}|{trend_direction}|{int(score)}"

        if bucket.get('signal_key') == signal_key and bucket.get('candle_index') == candle_index:
            bucket['count'] = int(bucket.get('count', 0)) + 1
        else:
            bucket = {
                'signal_key': signal_key,
                'side': side,
                'score': score,
                'price': price,
                'trend_direction': trend_direction,
                'candle_index': candle_index,
                'count': 1,
                'updated_at': datetime.now().isoformat(),
            }

        self.pending_signals[symbol] = bucket
        self._save_state()

        count = int(bucket.get('count', 0))
        if count < SIGNAL_CONFIRMATION_COUNT:
            return False, f"Waiting confirmation {count}/{SIGNAL_CONFIRMATION_COUNT}"

        self.pending_signals.pop(symbol, None)
        return True, f"Confirmed {count}/{SIGNAL_CONFIRMATION_COUNT}"

    def _update_position_excursions(self, symbol: str, current_price: float) -> None:
        pos = self.positions.get(symbol)
        if not pos or current_price <= 0:
            return
        entry_price = float(pos.get('entry_price', 0))
        if entry_price <= 0:
            return

        side = pos.get('side')
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        if side == 'DOWN':
            pnl_pct = -pnl_pct

        mfe_pct = max(float(pos.get('mfe_pct', 0.0)), pnl_pct)
        mae_pct = min(float(pos.get('mae_pct', 0.0)), pnl_pct)
        pos['mfe_pct'] = mfe_pct
        pos['mae_pct'] = mae_pct
        pos['last_price'] = current_price

    async def check_signal(
        self,
        symbol: str,
        score: float,
        pm_up_price: float,
        pm_down_price: float,
        candle_round: str = None,
        pm_up_token_id: Optional[str] = None,
        pm_down_token_id: Optional[str] = None,
        trend_direction: Optional[str] = None,
        signal_klines: Optional[list] = None,
        candle_index: Optional[int] = None,
    ):
        # Gunakan lock untuk mencegah race condition (multiple entries)
        global _trade_lock
        async with _trade_lock:
            print(
                f"[LOCK ACQUIRED] Checking signal for {symbol} | Score={score} | "
                f"UP={pm_up_price} | DOWN={pm_down_price} | Trend={trend_direction} | Round={candle_round}",
                flush=True,
            )
            print(
                f"[DEBUG] last_entry_round={self.last_entry_round}, candle_round={candle_round}, "
                f"match={self.last_entry_round == candle_round}",
                flush=True,
            )
            self._check_new_day()

            if self.trades_today >= MAX_TRADES_PER_DAY:
                print(f"[SKIP] Max trades reached: {self.trades_today}/{MAX_TRADES_PER_DAY}", flush=True)
                return None

            # Check if already entered this round (1 entry per round max)
            if candle_round and self.last_entry_round == candle_round:
                print(f"[SKIP] Already entered round {candle_round} - Blocking new entry!", flush=True)
                return None

            in_cooldown, cooldown_reason = self._is_in_cooldown(symbol, candle_index)
            if in_cooldown:
                print(f"[COOLDOWN] {cooldown_reason}", flush=True)
                return None

            market_filters = get_market_filters(signal_klines or [])
            if not market_filters.get('filter_ok', True):
                print(f"[CHOP FILTER] {market_filters.get('reason')}", flush=True)
                return None

            max_up, min_down, min_entry, max_down = get_price_limits(score)

            # Check if already have position for this symbol
            if symbol in self.positions:
                pos = self.positions[symbol]
                print(
                    f"[SKIP] Already have {pos['side']} position for {symbol} @ {pos['entry_price']:.4f} "
                    f"- Blocking new entry!",
                    flush=True,
                )
                return None

            print(
                f"[CHECK] No active position for {symbol}. Positions: {list(self.positions.keys())}. Checking signal...",
                flush=True,
            )

            if not trend_direction:
                trend_direction = self._get_current_trend(symbol)

            up_score_ok = ENABLE_UP_ENTRIES and UP_SCORE_MIN <= score <= UP_SCORE_MAX
            down_score_ok = ENABLE_DOWN_ENTRIES and DOWN_SCORE_MIN <= score <= DOWN_SCORE_MAX

            if up_score_ok and pm_up_price >= min_entry and pm_up_price <= max_up:
                is_valid, reason = get_entry_price_filter("UP", pm_up_price)
                if not is_valid:
                    print(f"[ENTRY FILTER] {reason}", flush=True)
                    return None

                trend_ok, trend_reason = check_trend_filter("UP", trend_direction)
                if not trend_ok:
                    print(f"[TREND FILTER] {trend_reason}", flush=True)
                    return None

                confirm_ok, confirm_reason = self._register_signal_confirmation(
                    symbol, "UP", candle_index, score, pm_up_price, trend_direction
                )
                print(f"[TREND CONFIRM] {trend_reason} | [ENTRY CONFIRM] {confirm_reason}", flush=True)
                if not confirm_ok:
                    return None

                return await self._execute_real_trade(
                    symbol,
                    "UP",
                    pm_up_price,
                    score,
                    candle_round=candle_round,
                    token_id=pm_up_token_id,
                    trend_direction=trend_direction,
                    filter_snapshot=market_filters,
                    candle_index=candle_index,
                )

            if down_score_ok and pm_down_price >= min_entry and pm_down_price <= max_down and pm_down_price >= min_down:
                is_valid, reason = get_entry_price_filter("DOWN", pm_down_price)
                if not is_valid:
                    print(f"[ENTRY FILTER] {reason}", flush=True)
                    return None

                trend_ok, trend_reason = check_trend_filter("DOWN", trend_direction)
                if not trend_ok:
                    print(f"[TREND FILTER] {trend_reason}", flush=True)
                    return None

                confirm_ok, confirm_reason = self._register_signal_confirmation(
                    symbol, "DOWN", candle_index, score, pm_down_price, trend_direction
                )
                print(f"[TREND CONFIRM] {trend_reason} | [ENTRY CONFIRM] {confirm_reason}", flush=True)
                if not confirm_ok:
                    return None

                return await self._execute_real_trade(
                    symbol,
                    "DOWN",
                    pm_down_price,
                    score,
                    candle_round=candle_round,
                    token_id=pm_down_token_id,
                    trend_direction=trend_direction,
                    filter_snapshot=market_filters,
                    candle_index=candle_index,
                )

            print(
                f"[SKIP] Score {score} outside allowed bands | "
                f"UP={UP_SCORE_MIN}-{UP_SCORE_MAX} enabled={ENABLE_UP_ENTRIES} | "
                f"DOWN={DOWN_SCORE_MIN}-{DOWN_SCORE_MAX} enabled={ENABLE_DOWN_ENTRIES}",
                flush=True,
            )
            return None

    async def _execute_real_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        score: float,
        candle_round: str = None,
        token_id: Optional[str] = None,
        trend_direction: Optional[str] = None,
        filter_snapshot: Optional[Dict[str, Any]] = None,
        candle_index: Optional[int] = None,
    ):
        print(f"\n🔥 REAL TRADE SIGNAL - {side} @ {price:.4f} (Score: {score}) Round: {candle_round}", flush=True)
        print(f"[DEBUG _execute] Called with candle_round={candle_round}", flush=True)
        print(f"[DEBUG _execute] executor_ready={self.executor_ready}, executor={self.executor is not None}", flush=True)

        size = TRADE_SIZE_USDC / price
        entry_cost = TRADE_SIZE_USDC
        gross_size = size / (1 - 0.005)  # Before 0.5% fee

        global _global_executor, _global_executor_initialized
        if not self.executor and _global_executor and _global_executor_initialized:
            self.executor = _global_executor
            self.executor_ready = True

        order_id = None
        mode = 'DRY-RUN'

        can_trade_live = bool(self.executor_ready and self.executor and token_id)
        if self.executor_ready and self.executor and not token_id:
            print(f"⚠️ Missing token_id for {side} entry - forcing dry-run", flush=True)

        if can_trade_live:
            print(f"   → LIVE EXECUTION on CLOB (EOA mode)")
            try:
                order_result = await self.executor.place_market_order(
                    token_id=token_id,
                    side="BUY",
                    size=size,
                    price=price
                )
                if order_result:
                    order_id = order_result.get('orderID', str(order_result))
                    mode = 'LIVE'
                    print(f"✅ CLOB ORDER PLACED: {order_id}")
                    self.balance -= entry_cost
                    print(f"💰 Balance deducted: ${entry_cost:.2f} → ${self.balance:.2f}")
                    self.trades_today += 1
                    self.total_trades += 1
                    self.last_entry_round = candle_round
                    print(f"[DEBUG LIVE] last_entry_round set to: {self.last_entry_round}", flush=True)
                    self._save_state()
                else:
                    print("❌ CLOB order returned None - recording as dry-run")
                    self.trades_today += 1
                    self.total_trades += 1
                    self.balance -= entry_cost
                    self.last_entry_round = candle_round
                    print(f"[DEBUG NONE] last_entry_round set to: {self.last_entry_round}", flush=True)
                    self._save_state()
            except Exception as e:
                print(f"❌ CLOB execution error: {e}")
                print("   → Recording as dry-run due to error")
                self.trades_today += 1
                self.total_trades += 1
                self.balance -= entry_cost
                self.last_entry_round = candle_round
                print(f"[DEBUG ERROR] last_entry_round set to: {self.last_entry_round}", flush=True)
                self._save_state()
        else:
            print("   → DRY-RUN (executor not ready or token_id missing)", flush=True)
            self.trades_today += 1
            self.total_trades += 1
            self.balance -= entry_cost
            self.last_entry_round = candle_round
            print(f"[DEBUG DRY] last_entry_round set to: {self.last_entry_round}", flush=True)
            print(f"[DEBUG DRY] About to call _save_state()", flush=True)
            self._save_state()
            print(f"[DEBUG DRY] _save_state() completed", flush=True)

        sl_pct, tp_pct = get_sl_tp_pct(score)

        sl_price = price * (1 - sl_pct / 100)
        tp_price = price * (1 + tp_pct / 100)

        self.positions[symbol] = {
            'side': side,
            'entry_price': price,
            'size': size,
            'entry_time': datetime.now().isoformat(),
            'entry_score': score,
            'cost': entry_cost,
            'sl_pct': sl_pct,
            'tp_pct': tp_pct,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'order_id': order_id,
            'token_id': token_id,
            'mode': mode,
            'trend_direction': trend_direction,
            'entry_round': candle_round,
            'entry_candle_index': candle_index,
            'filter_snapshot': filter_snapshot or {},
            'mfe_pct': 0.0,
            'mae_pct': 0.0,
            'last_price': price,
        }

        self._save_state()
        print(
            f"[POSITION SAVED] {symbol} {side} @ {price:.4f} | SL: {sl_price:.4f} | TP: {tp_price:.4f} | Mode: {mode}",
            flush=True,
        )

        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'side': side,
            'action': 'OPEN',
            'price': price,
            'size': size,
            'gross_size': gross_size,
            'cost': entry_cost,
            'entry_fee': entry_cost * 0.005,
            'entry_fee_pct': 0.5,
            'score': score,
            'balance_after': self.balance,
            'sl_pct': sl_pct,
            'tp_pct': tp_pct,
            'order_id': order_id,
            'token_id': token_id,
            'trend_direction': trend_direction,
            'mode': mode,
            'filter_snapshot': filter_snapshot or {},
            'candle_index': candle_index,
        }

    async def initialize_executor(self):
        """Initialize CLOB executor for real trading."""
        global _global_executor, _global_executor_initialized
        try:
            from polymarket_executor import PolymarketExecutor
            self.executor = PolymarketExecutor()
            await self.executor.initialize()
            _global_executor = self.executor
            _global_executor_initialized = True
            self.executor_ready = True
            print(f"✅ CLOB Executor initialized successfully")
            return True
        except Exception as e:
            print(f"⚠️ CLOB Executor init failed: {e}")
            self.executor_ready = False
            return False
    
    async def check_sl_tp(
        self,
        symbol: str,
        pm_up_price: float = 0.0,
        pm_down_price: float = 0.0,
        candle_index: Optional[int] = None,
    ):
        """Check SL/TP for existing positions."""
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        side = pos['side']
        entry_price = pos['entry_price']
        entry_score = pos.get('entry_score', 75)

        sl_pct = pos.get('sl_pct')
        tp_pct = pos.get('tp_pct')
        if sl_pct is None or tp_pct is None:
            sl_pct, tp_pct = get_sl_tp_pct(entry_score)

        sl_distance = entry_price * (sl_pct / 100)
        tp_distance = entry_price * (tp_pct / 100)
        
        if side == 'UP':
            sl_price = entry_price - sl_distance
            tp_price = entry_price + tp_distance
            current_price = pm_up_price
            self._update_position_excursions(symbol, current_price)
            
            if current_price >= tp_price:
                print(f"[TP TRIGGERED] {symbol} UP | Entry: {entry_price:.4f} | Current: {current_price:.4f} | TP: {tp_price:.4f}", flush=True)
                return await self._close_position_sl(symbol, current_price, 'TAKE_PROFIT', sl_pct, tp_pct, candle_index=candle_index)
            if current_price <= sl_price:
                print(f"[SL TRIGGERED] {symbol} UP | Entry: {entry_price:.4f} | Current: {current_price:.4f} | SL: {sl_price:.4f}", flush=True)
                return await self._close_position_sl(symbol, current_price, 'STOP_LOSS', sl_pct, tp_pct, candle_index=candle_index)
                
        else:  # DOWN
            # For DOWN: SL = price goes DOWN (loss), TP = price goes UP (win)
            sl_price = entry_price - sl_distance  # Price down = loss
            tp_price = entry_price + tp_distance   # Price up = win
            current_price = pm_down_price
            self._update_position_excursions(symbol, current_price)
            
            if current_price <= sl_price:
                print(f"[SL TRIGGERED] {symbol} DOWN | Entry: {entry_price:.4f} | Current: {current_price:.4f} | SL: {sl_price:.4f}", flush=True)
                return await self._close_position_sl(symbol, current_price, 'STOP_LOSS', sl_pct, tp_pct, candle_index=candle_index)
            if current_price >= tp_price:
                print(f"[TP TRIGGERED] {symbol} DOWN | Entry: {entry_price:.4f} | Current: {current_price:.4f} | TP: {tp_price:.4f}", flush=True)
                return await self._close_position_sl(symbol, current_price, 'TAKE_PROFIT', sl_pct, tp_pct, candle_index=candle_index)
        
        return None
    
    async def _close_position_sl(self, symbol: str, current_price: float, reason: str,
                           sl_pct: float = 20.0, tp_pct: float = 40.0,
                           candle_index: Optional[int] = None) -> Dict[str, Any]:
        """Close position with specified reason (SL or TP)."""
        global _trade_lock
        async with _trade_lock:
            if symbol not in self.positions:
                print(f"[CLOSE ERROR] {symbol} not in positions!", flush=True)
                return None

            pos = self.positions[symbol]
            side = pos['side']
            entry_price = pos['entry_price']
            size = pos['size']
            entry_cost = pos.get('cost', size * entry_price)
            position_mode = pos.get('mode', 'DRY-RUN')
            token_id = pos.get('token_id')

            print(
                f"[CLOSING] {symbol} {side} @ {entry_price:.4f} | Reason: {reason} | "
                f"Current: {current_price:.4f} | Mode: {position_mode}",
                flush=True,
            )

            exit_order_id = None
            if position_mode == 'LIVE' and self.executor_ready and self.executor and token_id:
                try:
                    print(f"   → LIVE CLOSE via SELL {size:.4f} shares", flush=True)
                    close_result = await self.executor.place_market_order(
                        token_id=token_id,
                        side="SELL",
                        size=size,
                        price=current_price,
                    )
                    if not close_result:
                        print("❌ Live close returned None", flush=True)
                        if KEEP_POSITION_OPEN_ON_FAILED_LIVE_CLOSE:
                            print("[LIVE CLOSE] Keeping position open because close failed", flush=True)
                            return None
                    else:
                        exit_order_id = close_result.get('orderID', str(close_result))
                        print(f"✅ LIVE CLOSE ORDER PLACED: {exit_order_id}", flush=True)
                except Exception as e:
                    print(f"❌ Live close failed: {e}", flush=True)
                    if KEEP_POSITION_OPEN_ON_FAILED_LIVE_CLOSE:
                        print("[LIVE CLOSE] Keeping position open because close raised exception", flush=True)
                        return None

            gross_exit_value = size * current_price
            exit_value = gross_exit_value
            pnl = exit_value - entry_cost

            if reason == 'TAKE_PROFIT':
                self.wins += 1
            elif reason == 'STOP_LOSS':
                self.losses += 1

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
                'balance_after': self.balance,
                'entry_score': pos.get('entry_score', 75),
                'sl_pct': sl_pct,
                'tp_pct': tp_pct,
                'entry_mode': position_mode,
                'exit_order_id': exit_order_id,
                'token_id': token_id,
                'mfe_pct': pos.get('mfe_pct', 0.0),
                'mae_pct': pos.get('mae_pct', 0.0),
                'entry_round': pos.get('entry_round'),
                'entry_candle_index': pos.get('entry_candle_index'),
                'exit_candle_index': candle_index,
                'filter_snapshot': pos.get('filter_snapshot', {}),
            }
            self.trade_history.append(trade)

            if reason == 'STOP_LOSS' and candle_index is not None and COOLDOWN_AFTER_SL_CANDLES > 0:
                self.cooldown_until_index[symbol] = candle_index + COOLDOWN_AFTER_SL_CANDLES + 1
                self.last_stop_loss_reason[symbol] = {
                    'at': datetime.now().isoformat(),
                    'cooldown_until_index': self.cooldown_until_index[symbol],
                    'mae_pct': pos.get('mae_pct', 0.0),
                    'mfe_pct': pos.get('mfe_pct', 0.0),
                }

            del self.positions[symbol]
            self._save_state()
            print(f"[POSITION CLOSED] {symbol} deleted. Remaining positions: {len(self.positions)}", flush=True)

            if _telegram_available:
                try:
                    asyncio.create_task(send_sl_tp_alert(
                        symbol=symbol,
                        side=side,
                        reason=reason,
                        entry_price=entry_price,
                        exit_price=current_price,
                        pnl=pnl,
                        balance=self.balance
                    ))
                    print(f"[TELEGRAM] SL/TP notification sent for {symbol}", flush=True)
                except Exception as e:
                    print(f"[TELEGRAM ERROR] Failed to send SL/TP notification: {e}", flush=True)

            return trade

    def get_status(self):
        total_closed = self.wins + self.losses
        win_rate = (self.wins / total_closed * 100) if total_closed > 0 else 0.0
        return {
            'balance': self.balance,
            'trades_today': self.trades_today,
            'total_trades': self.total_trades,
            'total_pnl': self.total_pnl,
            'pnl': self.total_pnl,
            'executor_ready': self.executor_ready,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'pending_signals': len(self.pending_signals),
            'cooldowns': dict(self.cooldown_until_index),
        }
    
    def get_daily_report(self) -> str:
        """Generate daily trading report with win rate."""
        total_closed = self.wins + self.losses
        win_rate = (self.wins / total_closed * 100) if total_closed > 0 else 0.0
        
        roi = ((self.balance - STARTING_BALANCE) / STARTING_BALANCE) * 100
        
        report = f"""
📊 **DAILY TRADING REPORT**

💰 Balance: ${self.balance:.2f} (ROI: {roi:+.2f}%)
📈 Total Trades: {self.trades_today}
🏆 Wins: {self.wins} | 🔻 Losses: {self.losses}
🎯 Win Rate: {win_rate:.1f}%
💵 Total PnL: ${self.total_pnl:+.2f}

📅 Date: {self.last_trade_date}
⏰ Updated: {datetime.now().strftime('%H:%M:%S')}
"""
        return report
    
    def format_trade_alert(self, trade: Dict[str, Any]) -> str:
        """Format trade notification for Telegram (PaperTrader compatible)."""
        emoji = "🟢" if trade['side'] == 'UP' else "🔴"
        
        if trade['action'] == 'OPEN':
            trade_mode = trade.get('mode', 'DRY-RUN')
            header = f"{emoji} <b>{trade_mode} TRADE - OPEN</b> {emoji}"
        elif trade.get('reason') == 'STOP_LOSS':
            header = f"⚠️ <b>STOP LOSS TRIGGERED</b> ⚠️"
            emoji = "🛑"
        elif trade.get('reason') == 'TAKE_PROFIT':
            header = f"🎯 <b>TAKE PROFIT HIT!</b> 🎯"
            emoji = "🎯"
        else:
            header = f"{emoji} <b>PAPER TRADE - CLOSE</b> {emoji}"
        
        # Get price - OPEN trades have 'price', CLOSE trades have 'exit_price'
        if trade['action'] == 'CLOSE':
            price = trade.get('exit_price', trade.get('price', 0))
        else:
            price = trade.get('price', 0)
        
        text = f"""
{header}

📊 Symbol: <code>{trade['symbol']}</code>
📥 {trade['action']}: <b>{trade['side']}</b> token
💵 Price: {price:.4f}
📏 Size: {trade['size']:.2f}
"""
        if 'cost' in trade:
            text += f"💸 Cost: ${trade['cost']:.2f}\n"
            
        if trade['action'] == 'OPEN':
            entry = trade['price']
            score = trade.get('score', 75)
            side = trade['side']
            sl_pct = trade.get('sl_pct', 20.0)
            tp_pct = trade.get('tp_pct', 40.0)
            
            if side == 'UP':
                sl_price = entry * (1 - sl_pct/100)
                tp_price = entry * (1 + tp_pct/100)
            else:
                sl_price = entry * (1 - sl_pct/100)
                tp_price = entry * (1 + tp_pct/100)
            
            conf = "🔥 SUPER YAKIN" if score >= 90 else "✅ SWEET SPOT" if 75 <= score <= 80 else "⚠️ FILTERED"
            text += f"\n{conf} (Score: {score})\n"
            text += f"🛑 SL: {sl_price:.4f} ({sl_pct:.0f}%)\n"
            text += f"🎯 TP: {tp_price:.4f} ({tp_pct:.0f}%)\n"
            text += f"📊 RR: 1:{tp_pct/sl_pct:.1f}\n"
            snapshot = trade.get('filter_snapshot') or {}
            if snapshot:
                ema_spread = snapshot.get('ema_spread_pct')
                atr_pct = snapshot.get('atr_pct')
                if ema_spread is not None:
                    text += f"🪄 EMA Spread: {ema_spread:.3f}%\n"
                if atr_pct is not None:
                    text += f"🌊 ATR: {atr_pct:.3f}%\n"
                
        if 'pnl' in trade:
            pnl_emoji = "🟢" if trade['pnl'] >= 0 else "🔴"
            text += f"{pnl_emoji} PnL: ${trade['pnl']:.2f}\n"
            
        if 'entry_price' in trade and trade['action'] == 'CLOSE':
            text += f"📊 Entry: {trade['entry_price']:.4f} → Exit: {trade['exit_price']:.4f}\n"
            if 'mfe_pct' in trade:
                text += f"🚀 MFE: {trade['mfe_pct']:+.2f}%\n"
            if 'mae_pct' in trade:
                text += f"🩸 MAE: {trade['mae_pct']:+.2f}%\n"
        
        if 'score' in trade:
            text += f"📈 Trend Score: {trade['score']:.0f}/100\n"
        
        text += f"💰 Balance: ${trade['balance_after']:.2f}\n"
        # Format candle time (15m window) like monitoring signal
        from datetime import datetime, timezone, timedelta
        try:
            # Get current ET time for candle calculation
            utc = datetime.now(timezone.utc)
            year = utc.year
            
            # Calculate ET offset (DST)
            mar1_dow = datetime(year, 3, 1).weekday()
            mar_sun = 1 + (6 - mar1_dow) % 7
            dst_start = datetime(year, 3, mar_sun + 7, 2, 0, 0, tzinfo=timezone.utc)
            nov1_dow = datetime(year, 11, 1).weekday()
            nov_sun = 1 + (6 - nov1_dow) % 7
            dst_end = datetime(year, 11, nov_sun, 6, 0, 0, tzinfo=timezone.utc)
            offset = timedelta(hours=-4) if dst_start <= utc < dst_end else timedelta(hours=-5)
            et_tz = timezone(offset)
            now_et = utc.astimezone(et_tz)
            
            # Calculate 15m candle window
            current_minute = now_et.minute
            candle_start_min = (current_minute // 15) * 15
            candle_end_min = candle_start_min + 15
            
            candle_start = now_et.replace(minute=candle_start_min, second=0, microsecond=0)
            if candle_end_min >= 60:
                candle_end = candle_start + timedelta(hours=1)
                candle_end = candle_end.replace(minute=0)
            else:
                candle_end = candle_start.replace(minute=candle_end_min)
            
            # Format candle times
            start_str = candle_start.strftime('%I:%M').lstrip('0')
            end_str = candle_end.strftime('%I:%M').lstrip('0')
            ampm = candle_start.strftime('%p')
            
            text += f"⏰ {start_str} - {end_str} {ampm} ET (15m)"
        except Exception as e:
            # Fallback: just show trade time
            ts = trade['timestamp']
            text += f"⏰ {ts[11:16] if len(ts) > 16 else ts[:5]} ET"
        
        return text
    
    def _check_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.last_trade_date:
            self.trades_today = 0
            self.last_trade_date = today
            self._save_state()

real_trader = RealTrader()
