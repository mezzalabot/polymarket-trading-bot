#!/usr/bin/env python3
"""
Telegram Notifier for Polymarket Trading Bot
Sends trade alerts and status updates to Telegram
"""

import os
import sys
import json
import asyncio
import aiohttp
from datetime import datetime
from typing import Optional, Dict, Any

# Telegram Bot API
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramNotifier:
    """Telegram bot notifier for trading alerts."""
    
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if not self.enabled:
            print("[Telegram] Notifier disabled - token or chat_id not set")
    
    async def send_message(self, text: str, parse_mode: str = 'HTML') -> bool:
        """Send message to Telegram chat with retry."""
        if not self.enabled:
            print("[Telegram] Notifier disabled - no token or chat_id")
            return False
        
        url = f"{TELEGRAM_API_BASE.format(token=self.bot_token)}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True
        }
        
        for attempt in range(3):  # retry up to 3 times
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    async with session.post(url, json=payload) as resp:
                        response_text = await resp.text()
                        if resp.status == 200:
                            print(f"[Telegram] SUCCESS: {text.splitlines()[0] if text else 'message'}")
                            return True
                        else:
                            print(f"[Telegram] Error: HTTP {resp.status} - {response_text[:100]}")
                            if attempt == 2:
                                return False
                            await asyncio.sleep(1 * (attempt + 1))
            except Exception as e:
                print(f"[Telegram] Attempt {attempt+1} failed: {e}")
                if attempt == 2:
                    return False
                await asyncio.sleep(1 * (attempt + 1))
        return False
    
    async def send_trade_alert(self, direction: str, market: str, price: float, 
                               size: float, edge_bps: float, pnl: Optional[float] = None):
        """Send trade execution alert."""
        emoji = "🟢" if direction == "UP" else "🔴"
        pnl_text = f"\n💰 PnL: ${pnl:.2f}" if pnl else ""
        
        text = f"""
{emoji} <b>TRADE EXECUTED</b> {emoji}

📊 Market: <code>{market}</code>
📈 Direction: <b>{direction}</b>
💵 Price: {price:.4f}
📏 Size: {size:.2f} units
⚡ Edge: {edge_bps:.1f} bps{pnl_text}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(text)
    
    async def send_signal_alert(self, signal: str, confidence: float, 
                                indicators: Dict[str, Any]):
        """Send signal detection alert."""
        emoji = "🚀" if signal == "BUY" else "🔻" if signal == "SELL" else "➖"
        
        text = f"""
{emoji} <b>SIGNAL DETECTED</b> {emoji}

🎯 Signal: <b>{signal}</b>
🎲 Confidence: {confidence:.1f}%

<b>Indicators:</b>
"""
        for key, val in indicators.items():
            text += f"  • {key}: {val}\n"
        
        text += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return await self.send_message(text)
    
    async def send_portfolio_update(self, positions: Dict[str, Any], 
                                    total_pnl: float):
        """Send portfolio status update."""
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        
        text = f"""
📊 <b>PORTFOLIO UPDATE</b> 📊

💰 Total PnL: {pnl_emoji} ${total_pnl:.2f}

<b>Positions:</b>
"""
        for pos, data in positions.items():
            text += f"  • {pos}: {data.get('qty', 0):.2f} @ ${data.get('avg_px', 0):.4f}\n"
        
        text += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return await self.send_message(text)
    
    async def send_error_alert(self, error_msg: str):
        """Send error notification."""
        text = f"""
⚠️ <b>ERROR ALERT</b> ⚠️

❌ {error_msg}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send_message(text)
    
    async def send_startup_message(self, symbol: str, timeframe: str):
        """Send bot startup notification."""
        text = f"""
🤖 <b>TRADING BOT STARTED</b> 🤖

📊 Symbol: <code>{symbol}</code>
⏱️ Timeframe: {timeframe}
📡 Polymarket: Connected
💾 Recording: Enabled

✅ Bot is running and monitoring markets...
"""
        return await self.send_message(text)


# Simple functions for direct use
async def send_strong_signal(symbol: str, tf: str, direction: str, score: float, price: float):
    """Send strong signal alert."""
    notifier = TelegramNotifier()
    
    emoji = "🚀 STRONG BULL" if direction == "BULLISH" else "🔻 STRONG BEAR"
    conf = "🔥" if score >= 90 else "✅"
    
    text = f"""
{emoji} <b>SIGNAL DETECTED</b> {emoji}

📊 Symbol: <code>{symbol}</code>
⏱️ Timeframe: {tf}
📈 Direction: <b>{direction}</b>
🎲 Score: {score}/100 {conf}
💵 Price: {price:.4f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    return await notifier.send_message(text)


async def send_trend_change(symbol: str, tf: str, new_direction: str, old_direction: str, score: float):
    """Send trend change alert."""
    notifier = TelegramNotifier()
    
    text = f"""
⚡ <b>TREND CHANGE</b> ⚡

📊 Symbol: <code>{symbol}</code>
⏱️ Timeframe: {tf}
🔄 {old_direction} → <b>{new_direction}</b>
🎲 Score: {score}/100

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    return await notifier.send_message(text)


async def shutdown_notifier():
    """Send shutdown notification."""
    notifier = TelegramNotifier()
    text = "🛑 <b>Bot shutting down...</b>"
    return await notifier.send_message(text)


async def send_sl_tp_alert(symbol: str, side: str, reason: str, entry_price: float, 
                           exit_price: float, pnl: float, balance: float):
    """Send SL/TP hit notification."""
    notifier = TelegramNotifier()
    
    if reason == 'TAKE_PROFIT':
        emoji = "🎯"
        title = "TAKE PROFIT HIT"
        result = "✅ WIN"
    else:
        emoji = "🛑"
        title = "STOP LOSS HIT"
        result = "❌ LOSS"
    
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    
    text = f"""
{emoji} <b>{title}</b> {emoji}

{result} | {side} Position Closed

📊 Symbol: <code>{symbol}</code>
📥 Entry: {entry_price:.4f}
📤 Exit: {exit_price:.4f}
{pnl_emoji} PnL: ${pnl:+.2f}
💰 Balance: ${balance:.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    return await notifier.send_message(text)


async def send_message(text: str):
    """Send custom message."""
    notifier = TelegramNotifier()
    return await notifier.send_message(text)


# Test function
async def test_notifier():
    """Test the Telegram notifier."""
    notifier = TelegramNotifier()
    
    if not notifier.enabled:
        print("Telegram notifier not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.")
        return
    
    print("Sending test messages...")
    
    await notifier.send_startup_message("BTCUSDT", "15m")
    await asyncio.sleep(1)
    
    await notifier.send_signal_alert("BUY", 75.5, {
        "RSI": "32.5 (oversold)",
        "MACD": "bullish crossover",
        "Volume": "1.2x avg"
    })
    await asyncio.sleep(1)
    
    await notifier.send_trade_alert("UP", "BTC-15m-UP", 0.52, 1.0, 35.0)
    
    print("Test messages sent!")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        asyncio.run(test_notifier())
    else:
        print("Usage: python telegram_notifier.py test")
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.")
