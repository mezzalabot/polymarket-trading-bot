import sys
import os

# CRITICAL: Add parent dir to path for clob_patch
sys.path.insert(0, os.path.expanduser("~/polymarket-bot"))

# Apply monkey-patch BEFORE any py_clob_client imports
import clob_patch

import asyncio
from datetime import datetime, timezone, timedelta

import rich

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.live import Live

import config
from src import feeds
import dashboard

from dotenv import load_dotenv
from telegram_notifier import send_strong_signal, send_trend_change, shutdown_notifier, send_message
from real_trading import RealTrader, calculate_trend_direction

from paper_trading import PaperTrader  # keep for now as fallback

load_dotenv()

# Initialize REAL trader with new wallet
real_data_dir = os.path.expanduser("~/polymarket-bot/real_data")
real_trader = RealTrader(data_dir=real_data_dir)

# Use real trader as primary
paper_trader = real_trader  # Real mode active

console = Console(force_terminal=True)

TELEGRAM_ENABLED = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))

class DashboardState:
    def __init__(self):
        self.last_direction = {}
        self.last_strong_notify = {}
        self.last_change_notify = {}
        self.last_neutral_notify = {}

    def should_notify_strong(self, symbol: str, tf: str):
        key = f"{symbol}_{tf}"
        now = asyncio.get_event_loop().time()
        last = self.last_strong_notify.get(key, 0)
        return now - last > int(os.getenv("ANTI_SPAM_STRONG_SEC", 180))

    def update_strong_notify(self, symbol: str, tf: str):
        key = f"{symbol}_{tf}"
        self.last_strong_notify[key] = asyncio.get_event_loop().time()

    def should_notify_neutral(self, symbol: str, tf: str):
        """Check if we should send neutral status update (every 3 min)."""
        key = f"{symbol}_{tf}"
        now = asyncio.get_event_loop().time()
        last = self.last_neutral_notify.get(key, 0)
        return now - last > 180  # 3 minutes

    def update_neutral_notify(self, symbol: str, tf: str):
        key = f"{symbol}_{tf}"
        self.last_neutral_notify[key] = asyncio.get_event_loop().time()

    def check_trend_change(self, symbol: str, tf: str, new_direction: str, score: float):
        key = f"{symbol}_{tf}"
        old = self.last_direction.get(key, "NEUTRAL")
        self.last_direction[key] = new_direction

        if old == new_direction:
            return None

        if new_direction == "NEUTRAL":
            return None

        threshold = int(os.getenv("TREND_CHANGE_THRESHOLD", 55))
        if abs(score - 50) < threshold:
            return None

        change_key = f"change_{symbol}_{tf}"
        now = asyncio.get_event_loop().time()
        last_change = self.last_change_notify.get(change_key, 0)
        if now - last_change < int(os.getenv("ANTI_SPAM_CHANGE_SEC", 300)):
            return None

        self.last_change_notify[change_key] = now
        return old


dash_state = DashboardState()


def _pm_feed_ok(state: feeds.State) -> bool:
    if not getattr(state, "pm_feed_connected", False):
        return False
    if getattr(state, "pm_feed_guard_active", False):
        return False
    last_quote_ts = float(getattr(state, "pm_last_quote_ts", 0.0) or 0.0)
    if last_quote_ts and (datetime.now().timestamp() - last_quote_ts) > 20:
        return False
    pm_up = getattr(state, "pm_up", None)
    pm_dn = getattr(state, "pm_dn", None)
    if pm_up is None or pm_dn is None:
        return False
    if not (0.0 < pm_up < 1.0 and 0.0 < pm_dn < 1.0):
        return False
    return True


def _pm_feed_label(state: feeds.State) -> str:
    status = "CONNECTED" if getattr(state, "pm_feed_connected", False) else "DISCONNECTED"
    if getattr(state, "pm_feed_guard_active", False):
        status += " | GUARD"
    reason = getattr(state, "pm_last_refresh_reason", "") or getattr(state, "pm_last_error", "")
    if reason:
        status += f" | {reason}"
    return status



def get_strong_reasons(indicators):
    reasons = []
    if o := indicators.get("order_book_imbalance"):
        if abs(o) > 12:
            reasons.append(f"OBI {o:+.0f}%")
    if c := indicators.get("cvd_5m", 0):
        if abs(c) > 2_500_000:
            reasons.append(f"CVD {'+' if c > 0 else ''}{c/1e6:.1f}M")
    if r := indicators.get("rsi"):
        if r > 72 or r < 28:
            reasons.append(f"RSI {r:.0f}")
    if indicators.get("macd_cross_bullish"):
        reasons.append("MACD ↑ cross")
    if indicators.get("macd_cross_bearish"):
        reasons.append("MACD ↓ cross")
    return " • ".join(reasons) if reasons else ""


def pick(title: str, options: list[str]) -> str:
    console.print(f"\n[bold]{title}[/bold]")
    for i, o in enumerate(options, 1):
        console.print(f"  [{i}] {o}")
    while True:
        raw = input("  → ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        console.print("  [red]invalid – try again[/red]")


def _build_candle_round(now_et: datetime):
    current_minute = now_et.minute
    candle_start_min = (current_minute // 15) * 15
    candle_end_min = candle_start_min + 15
    candle_start = now_et.replace(minute=candle_start_min, second=0, microsecond=0)

    if candle_end_min >= 60:
        candle_end = candle_start + timedelta(hours=1)
        candle_end = candle_end.replace(minute=0)
    else:
        candle_end = candle_start.replace(minute=candle_end_min)

    start_hour = candle_start.strftime('%I:%M').lstrip('0')
    end_hour = candle_end.strftime('%I:%M').lstrip('0')
    ampm = candle_start.strftime('%p')
    candle_round = f"{start_hour}-{end_hour}_{ampm}"
    time_left = 15 - (current_minute % 15)
    return candle_round, start_hour, end_hour, ampm, time_left


async def position_monitor_loop(state: feeds.State, coin: str, tf: str, interval: float = 2.0):
    await asyncio.sleep(2)
    symbol_key = f"{coin}-{tf}"

    while True:
        try:
            if hasattr(paper_trader, 'check_sl_tp'):
                sltp_trade = await paper_trader.check_sl_tp(
                    symbol_key,
                    state.pm_up or 0,
                    state.pm_dn or 0,
                )
            else:
                sltp_trade = None

            if sltp_trade and TELEGRAM_ENABLED:
                try:
                    alert_text = paper_trader.format_trade_alert(sltp_trade)
                    await send_message(alert_text)
                    reason = sltp_trade.get('reason', 'CLOSE')
                    print(f"[SL/TP NOTIFICATION] {reason} for {sltp_trade.get('side')} - SENT", flush=True)
                except Exception as e:
                    print(f"[ERROR] SL/TP notification failed: {e}", flush=True)
        except Exception as e:
            print(f"[ERROR] Position monitor failed: {e}", flush=True)

        await asyncio.sleep(interval)


async def display_loop(state: feeds.State, trend_state: feeds.State, coin: str, tf: str):
    await asyncio.sleep(2)
    refresh_interval = config.REFRESH_5M if tf == "5m" else config.REFRESH

    console.print("[dim]Bot running in background mode (logs only)...[/dim]\n")
    print("🔴 FULL REAL MODE ACTIVE", flush=True)
    print(f"📊 Loading REAL state from: {real_data_dir}", flush=True)
    print(f"💰 REAL Balance: ${real_trader.balance:.2f} | Trades: {real_trader.total_trades} | PnL: ${real_trader.total_pnl:.2f}", flush=True)
    print(f"🔑 Wallet: 0x39d3...7611", flush=True)

    while True:
        try:
            if state.mid > 0 and state.klines:
                score = 50
                try:
                    if hasattr(dashboard, "calculate_trend_score"):
                        score = dashboard.calculate_trend_score(state)
                except Exception as e:
                    print(f"[ERROR] Score calculation failed: {e}", flush=True)

                direction = "NEUTRAL"
                if score > 60:
                    direction = "BULLISH"
                elif score < 40:
                    direction = "BEARISH"

                trend_direction = "NEUTRAL"
                try:
                    if trend_state.klines:
                        trend_direction = calculate_trend_direction(trend_state.klines, lookback=6)
                except Exception as e:
                    print(f"[ERROR] HTF trend calculation failed: {e}", flush=True)

                try:
                    old_dir = dash_state.check_trend_change(coin, tf, direction, score)
                    if old_dir and TELEGRAM_ENABLED:
                        await send_trend_change(
                            symbol=coin.upper(),
                            timeframe=tf,
                            old_direction=old_dir,
                            new_direction=direction,
                            score=score
                        )
                except Exception as e:
                    print(f"[ERROR] Trend change notify failed: {e}", flush=True)

                et_offset = timedelta(hours=-4)
                et_tz = timezone(et_offset)
                now_et = datetime.now(et_tz)
                candle_round, start_hour, end_hour, ampm, time_left = _build_candle_round(now_et)

                should_notify = dash_state.should_notify_neutral(coin, tf)
                print(f"[DEBUG] should_notify={should_notify}, TELEGRAM_ENABLED={TELEGRAM_ENABLED}, time_since_last={asyncio.get_event_loop().time() - dash_state.last_neutral_notify.get(f'{coin}_{tf}', 0):.0f}s", flush=True)
                if should_notify and TELEGRAM_ENABLED:
                    try:
                        print(f"[DEBUG] Sending monitoring notification...", flush=True)
                        status = paper_trader.get_status()
                        if state.pm_up is None:
                            pm_up_str = "⏳ Waiting data..."
                        elif state.pm_up <= 0 or state.pm_up >= 1:
                            pm_up_str = "🔒 Market closed"
                        else:
                            pm_up_str = f"${state.pm_up:.4f}"

                        if state.pm_dn is None:
                            pm_dn_str = "⏳ Waiting data..."
                        elif state.pm_dn <= 0 or state.pm_dn >= 1:
                            pm_dn_str = "🔒 Market closed"
                        else:
                            pm_dn_str = f"${state.pm_dn:.4f}"

                        date_str = now_et.strftime('%B %d')
                        current_time = now_et.strftime('%I:%M %p').lstrip('0')

                        position_info = ""
                        symbol_key = f"{coin}-{tf}"
                        if hasattr(paper_trader, 'positions') and symbol_key in paper_trader.positions:
                            pos = paper_trader.positions[symbol_key]
                            pos_side = pos.get('side', '?')
                            pos_price = pos.get('entry_price', 0)
                            pos_sl = pos.get('sl_price', 0)
                            pos_tp = pos.get('tp_price', 0)
                            size = pos.get('size', 0)
                            if pos_side == 'UP':
                                current_pnl = ((state.pm_up or 0) - pos_price) * size
                            else:
                                current_pnl = ((state.pm_dn or 0) - pos_price) * size
                            pnl_emoji = "🟢" if current_pnl >= 0 else "🔴"
                            position_info = (
                                f"\n📍 <b>POSITION ACTIVE</b>\n{pos_side} @ {pos_price:.4f} | {pnl_emoji} PnL: ${current_pnl:+.2f}\n"
                                f"🛑 SL: {pos_sl:.4f} | 🎯 TP: {pos_tp:.4f}\n"
                            )

                        await send_message(
                            f"👀 <b>MONITORING - Bitcoin {tf.upper()}</b>\n\n"
                            f"📅 {date_str} | ⏰ {start_hour} - {end_hour} {ampm} ET\n\n"
                            f"📊 BTC ${state.mid:,.0f}\n"
                            f"📈 Polymarket UP: {pm_up_str}\n"
                            f"📉 Polymarket DOWN: {pm_dn_str}\n"
                            f"🎯 Direction: {direction} (Score: {score})\n"
                            f"🧭 HTF Trend: {trend_direction}\n"
                            f"📡 Feed: {_pm_feed_label(state)}\n"
                            f"⏱️ Time Left: {time_left} min"
                            f"{position_info}\n"
                            f"💰 Balance: ${status['balance']:.2f} | Tahap 1B: {status['total_trades']}/20 | PnL: ${status['total_pnl']:.2f}\n"
                            f"🤖 Bot: RUNNING | ⏰ {current_time} ET"
                        )
                        dash_state.update_neutral_notify(coin, tf)
                    except Exception as e:
                        print(f"[ERROR] Neutral notification failed: {type(e).__name__}: {e}", flush=True)
                        # Continue even if notification fails - don't break trading
                        pass

                trade_executed = False
                trade = None
                if not _pm_feed_ok(state):
                    print(f"[PM GUARD] Skip entry | {_pm_feed_label(state)}", flush=True)
                else:
                    try:
                        trade = await paper_trader.check_signal(
                            f"{coin}-{tf}",
                            score,
                            state.pm_up or 0,
                            state.pm_dn or 0,
                            candle_round,
                            state.pm_up_id,
                            state.pm_dn_id,
                            trend_direction,
                        )
                        if trade:
                            trade_executed = True
                            print(f"[TRADE EXECUTED] {trade.get('side')} @ {trade.get('price',0):.4f}", flush=True)
                    except Exception as e:
                        print(f"[ERROR] Trade execution failed: {e}", flush=True)

                if trade_executed and trade and TELEGRAM_ENABLED:
                    try:
                        emoji = "🚀" if score >= 75 else "🔻"
                        conf_text = "✅ SWEET SPOT" if 75 <= score <= 80 else "⚠️ FILTERED"

                        signal_text = f"""{emoji} <b>SIGNAL ENTRY - EXECUTED</b> {emoji}

📊 Symbol: <code>{coin.upper()}</code>
⏱️ Timeframe: {tf}
📈 Direction: <b>{direction}</b>
🧭 HTF Trend: <b>{trend_direction}</b>
🎲 Score: {score}/100
{conf_text}

⚡ Trade Executed Successfully
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET"""

                        await send_message(signal_text)

                        if trade and trade.get('side') and trade.get('price'):
                            try:
                                if hasattr(paper_trader, 'format_trade_alert'):
                                    alert_text = paper_trader.format_trade_alert(trade)
                                else:
                                    emoji = "🟢" if trade['side'] == 'UP' else "🔴"
                                    sl_pct = trade.get('sl_pct', 15)
                                    tp_pct = trade.get('tp_pct', 30)
                                    entry = trade['price']
                                    sl_price = entry * (1 - sl_pct / 100)
                                    tp_price = entry * (1 + tp_pct / 100)
                                    conf = "✅ SWEET SPOT" if 75 <= trade.get('score', 75) <= 80 else "⚠️ FILTERED"

                                    alert_text = f"""{emoji} <b>{trade.get('mode', 'DRY-RUN')} TRADE - OPEN</b> {emoji}

📊 Symbol: <code>{trade['symbol']}</code>
📥 OPEN: <b>{trade['side']}</b> token
💵 Price: {trade['price']:.4f}
📏 Size: {trade['size']:.2f}
💸 Cost: ${trade.get('cost', 5.0):.2f}

{conf} (Score: {trade.get('score', 75)})
🛑 SL: {sl_price:.4f} ({sl_pct:.0f}%)
🎯 TP: {tp_price:.4f} ({tp_pct:.0f}%)
📊 RR: 1:{tp_pct/sl_pct:.1f}
🧭 HTF Trend: {trade.get('trend_direction', trend_direction)}

💰 Balance: ${trade['balance_after']:.2f}
⏰ {trade['timestamp'][:19]} ET"""
                            except Exception as e:
                                print(f"[ERROR] Format trade alert failed: {e}", flush=True)
                                alert_text = f"REAL TRADE: {trade.get('side')} {trade.get('action')} @ {trade.get('price',0):.4f}"

                            try:
                                await send_message(alert_text)
                                print(f"[TELEGRAM] Entry notification SENT successfully", flush=True)
                            except Exception as e:
                                print(f"[TELEGRAM ERROR] Failed to send entry notification: {e}", flush=True)

                        dash_state.update_strong_notify(coin, tf)
                        print(f"[NOTIFY COMPLETE] Signal + Trade for {direction} {score} | HTF={trend_direction}", flush=True)
                    except Exception as e:
                        print(f"[ERROR] Notification failed: {e}", flush=True)

                try:
                    now = datetime.now().strftime("%H:%M:%S")
                    status = paper_trader.get_status()
                    print(
                        f"[{now}] BTC={state.mid:,.0f} | Score={score:.0f} | {direction} | "
                        f"HTF={trend_direction} | Tahap1B={status['total_trades']}/20 | PnL=${status['total_pnl']:.2f}",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[ERROR] Logging failed: {e}", flush=True)

        except Exception as e:
            print(f"[CRITICAL ERROR] Main loop failed: {e}", flush=True)

        await asyncio.sleep(refresh_interval)


async def main():
    # AUTO MODE: BTC 15m (non-interactive for 24/7 trading)
    coin = "BTC"  # Auto-select BTC
    tf = "15m"    # Auto-select 15m timeframe
    
    console.print(f"\n[bold magenta]═══ CRYPTO PREDICTION BOT ═══[/bold magenta]\n")
    console.print(f"[bold green]Auto-selected: {coin} {tf}[/bold green]\n")

    console.print(f"\n[bold green]Starting {coin} {tf} …[/bold green]\n")
    console.print("[bold red]MODE: FULL LIVE - REAL POLYMARKET EXECUTION[/bold red]\n")
    
    # Initialize CLOB executor for real trading
    try:
        console.print("[bold yellow]Initializing Polymarket CLOB executor...[/bold yellow]")
        await real_trader.initialize_executor()
        if real_trader.executor_ready:
            console.print("[bold green]✅ CLOB Executor READY - Real orders will be placed![/bold green]\n")
        else:
            console.print("[bold red]⚠️ CLOB Executor failed - Falling back to dry-run[/bold red]\n")
    except Exception as e:
        console.print(f"[bold red]❌ Executor init error: {e}[/bold red]\n")

    state = feeds.State()
    trend_state = feeds.State()

    if hasattr(feeds, "fetch_pm_tokens_robust"):
        state.pm_up_id, state.pm_dn_id, state.pm_market_slug = feeds.fetch_pm_tokens_robust(coin, tf)
    else:
        state.pm_up_id, state.pm_dn_id = feeds.fetch_pm_tokens(coin, tf)
    if state.pm_up_id:
        console.print(f"  [PM] Up   → {state.pm_up_id[:24]}…")
        console.print(f"  [PM] Down → {state.pm_dn_id[:24]}…")
    else:
        console.print("  [yellow][PM] no market for this coin/timeframe – prices will not show[/yellow]")

    binance_sym = config.COIN_BINANCE[coin]
    kline_iv = config.TF_KLINE[tf]
    trend_kline_iv = "1h"

    console.print("  [Binance] bootstrapping signal candles …")
    await feeds.bootstrap(binance_sym, kline_iv, state)
    console.print("  [Binance] bootstrapping HTF trend candles (1h) …")
    await feeds.bootstrap(binance_sym, trend_kline_iv, trend_state)

    await asyncio.gather(
        feeds.ob_poller(binance_sym, state),
        feeds.binance_feed(binance_sym, kline_iv, state),
        feeds.binance_feed(binance_sym, trend_kline_iv, trend_state),
        feeds.pm_feed(state, coin, tf),
        position_monitor_loop(state, coin, tf),
        display_loop(state, trend_state, coin, tf),
    )


if __name__ == "__main__":
    # Skip rich console update on Linux (Windows-specific)
    if sys.platform == "win32":
        version = rich.version()
        if version:
            client = rich.init()
            rich.print_style(client)
            rich.close(client)
            sys.exit(1)
        else:
            rich.update()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        if TELEGRAM_ENABLED:
            asyncio.run(shutdown_notifier())