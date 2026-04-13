#!/usr/bin/env python3
import os
import asyncio
from datetime import datetime
from html import escape
import aiohttp
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

_session = None

def _enabled():
    return bool(BOT_TOKEN and CHAT_ID)

async def _get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _session

async def send_message(text: str):
    if not _enabled():
        print("[Telegram] Notifier disabled - token or chat_id not set", flush=True)
        return False
    session = await _get_session()
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    async with session.post(f"{BASE_URL}/sendMessage", data=payload) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"Telegram API {resp.status}: {body}")
        return True

async def send_signal_alert(symbol: str, timeframe: str, side: str, score: float, trend_direction: str, price: float, candle_round: str):
    emoji = "🟢" if side == "UP" else "🔴"
    strength = "✅ VALID SIGNAL" if 75 <= float(score) <= 80 else "⚠️ VALID SIGNAL"
    text = (
        f"{emoji} <b>{strength}</b> {emoji}\n\n"
        f"📊 Symbol: <code>{escape(symbol)}</code>\n"
        f"⏱️ Timeframe: {escape(timeframe)}\n"
        f"📈 Side: <b>{escape(side)}</b>\n"
        f"🎲 Score: {float(score):.0f}/100\n"
        f"🧭 HTF Trend: <b>{escape(trend_direction)}</b>\n"
        f"💵 Trigger Price: {float(price):.4f}\n"
        f"🕒 Round: {escape(candle_round)}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return await send_message(text)

async def send_trade_alert(trade: dict):
    side = trade.get("side", "?")
    emoji = "🟢" if side == "UP" else "🔴"
    score = float(trade.get("score", trade.get("entry_score", 75)))
    entry = float(trade.get("price", trade.get("entry_price", 0.0)))
    sl_pct = float(trade.get("sl_pct", 15))
    tp_pct = float(trade.get("tp_pct", 30))
    sl_price = entry * (1 - sl_pct / 100)
    tp_price = entry * (1 + tp_pct / 100)
    text = (
        f"{emoji} <b>{escape(trade.get('mode', 'DRY-RUN'))} ENTRY EXECUTED</b> {emoji}\n\n"
        f"📊 Symbol: <code>{escape(trade.get('symbol', ''))}</code>\n"
        f"📥 Entry: <b>{escape(side)}</b> token\n"
        f"💵 Entry Price: {entry:.4f}\n"
        f"📏 Size: {float(trade.get('size', 0)):.2f}\n"
        f"💸 Cost: ${float(trade.get('cost', 0)):.2f}\n\n"
        f"🎲 Score: {score:.0f}/100\n"
        f"🧭 HTF Trend: <b>{escape(trade.get('trend_direction', 'NEUTRAL'))}</b>\n"
        f"🛑 SL: {sl_price:.4f} ({sl_pct:.0f}%)\n"
        f"🎯 TP: {tp_price:.4f} ({tp_pct:.0f}%)\n"
        f"📊 RR: 1:{tp_pct / sl_pct:.1f}\n\n"
        f"💰 Balance: ${float(trade.get('balance_after', 0)):.2f}\n"
        f"⏰ {str(trade.get('timestamp', ''))[:19]}"
    )
    return await send_message(text)

async def send_sl_tp_alert(symbol: str, side: str, reason: str, entry_price: float, exit_price: float, pnl: float, balance: float):
    emoji = "🎯" if reason == "TAKE_PROFIT" else "🛑"
    title = "TAKE PROFIT HIT" if reason == "TAKE_PROFIT" else "STOP LOSS HIT"
    text = (
        f"{emoji} <b>{title}</b> {emoji}\n\n"
        f"📊 Symbol: <code>{escape(symbol)}</code>\n"
        f"📈 Side: <b>{escape(side)}</b>\n"
        f"💵 Entry: {float(entry_price):.4f}\n"
        f"💰 Exit: {float(exit_price):.4f}\n"
        f"📉 PnL: ${float(pnl):+.2f}\n"
        f"💼 Balance: ${float(balance):.2f}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return await send_message(text)

async def send_trend_change(symbol: str, timeframe: str, old_direction: str, new_direction: str, score: float):
    text = (
        f"🔄 <b>TREND CHANGE</b>\n\n"
        f"📊 Symbol: <code>{escape(symbol)}</code>\n"
        f"⏱️ Timeframe: {escape(timeframe)}\n"
        f"⬅️ From: <b>{escape(old_direction)}</b>\n"
        f"➡️ To: <b>{escape(new_direction)}</b>\n"
        f"🎲 Score: {float(score):.0f}/100"
    )
    return await send_message(text)

async def send_strong_signal(*args, **kwargs):
    return True

async def shutdown_notifier():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None

if __name__ == "__main__":
    import sys
    async def _main():
        if len(sys.argv) > 1 and sys.argv[1] == 'test':
            ok = await send_message('✅ Telegram notifier test berhasil')
            print('OK' if ok else 'FAILED')
        else:
            print('Usage: python telegram_notifier.py test')
    asyncio.run(_main())
