#!/usr/bin/env python3
"""
Monitoring Dashboard for Dry Run Tracking
Tracks 30-50 trades with new GPT-5.4 config
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any

class DryRunMonitor:
    def __init__(self, state_file: str = "~/polymarket-bot/real_data/real_state.json"):
        self.state_file = os.path.expanduser(state_file)
        self.trade_history: List[Dict] = []
        self.load_state()
    
    def load_state(self):
        """Load current state from real_trading."""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                self.trade_history = state.get('trade_history', [])
        except Exception as e:
            print(f"[ERROR] Could not load state: {e}")
            self.trade_history = []
    
    def analyze_since_restart(self, restart_time: str = "2026-04-13T20:00:00"):
        """Analyze trades since GPT-5.4 patch restart."""
        restart_dt = datetime.fromisoformat(restart_time)
        
        recent_trades = [
            t for t in self.trade_history 
            if datetime.fromisoformat(t.get('timestamp', '2000-01-01')) > restart_dt
        ]
        
        if not recent_trades:
            print("⏳ No trades since restart yet...")
            return None
        
        # Analysis
        up_trades = [t for t in recent_trades if t.get('side') == 'UP']
        wins = len([t for t in recent_trades if t.get('reason') == 'TAKE_PROFIT'])
        losses = len([t for t in recent_trades if t.get('reason') == 'STOP_LOSS'])
        total_pnl = sum(t.get('pnl', 0) for t in recent_trades)
        
        win_rate = (wins / len(recent_trades) * 100) if recent_trades else 0
        
        report = {
            'total_trades': len(recent_trades),
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'up_trades': len(up_trades),
            'avg_pnl_per_trade': total_pnl / len(recent_trades) if recent_trades else 0,
        }
        
        return report
    
    def print_dashboard(self):
        """Print monitoring dashboard."""
        self.load_state()
        report = self.analyze_since_restart()
        
        if not report:
            print("\n" + "="*50)
            print("📊 DRY RUN MONITOR - GPT-5.4 CONFIG")
            print("="*50)
            print("⏳ Waiting for first trade...")
            print("="*50)
            return
        
        print("\n" + "="*50)
        print("📊 DRY RUN MONITOR - GPT-5.4 CONFIG")
        print("="*50)
        print(f"🎯 Target: 30-50 trades for validation")
        print(f"📈 Current: {report['total_trades']} trades")
        print(f"✅ Wins: {report['wins']} | ❌ Losses: {report['losses']}")
        print(f"🎯 Win Rate: {report['win_rate']:.1f}%")
        print(f"💰 Total PnL: ${report['total_pnl']:+.2f}")
        print(f"📊 Avg per Trade: ${report['avg_pnl_per_trade']:+.2f}")
        print(f"🟢 UP Trades Only: {report['up_trades']}")
        print("="*50)
        
        # Progress bar
        progress = min(report['total_trades'] / 30 * 100, 100)
        bar = "█" * int(progress/5) + "░" * (20 - int(progress/5))
        print(f"⏳ Progress to 30 trades: [{bar}] {progress:.0f}%")
        
        if report['total_trades'] >= 30:
            print("\n✅ TARGET REACHED! Ready for evaluation.")
            if report['win_rate'] >= 40 and report['total_pnl'] > 0:
                print("🚀 POSITIVE EXPECTANCY CONFIRMED!")
                print("Ready for next phase: Paper Execution Parity Check")
            else:
                print("⚠️ Need more optimization before live.")
        
        print("="*50)

if __name__ == "__main__":
    monitor = DryRunMonitor()
    monitor.print_dashboard()
