--- /mnt/data/main.py	2026-04-14 15:33:33.835102056 +0000
+++ /mnt/data/main_feed_recovery.py	2026-04-14 15:39:58.913037645 +0000
@@ -100,6 +100,34 @@
 dash_state = DashboardState()
 
 
+def _pm_feed_ok(state: feeds.State) -> bool:
+    if not getattr(state, "pm_feed_connected", False):
+        return False
+    if getattr(state, "pm_feed_guard_active", False):
+        return False
+    last_quote_ts = float(getattr(state, "pm_last_quote_ts", 0.0) or 0.0)
+    if last_quote_ts and (datetime.now().timestamp() - last_quote_ts) > 20:
+        return False
+    pm_up = getattr(state, "pm_up", None)
+    pm_dn = getattr(state, "pm_dn", None)
+    if pm_up is None or pm_dn is None:
+        return False
+    if not (0.0 < pm_up < 1.0 and 0.0 < pm_dn < 1.0):
+        return False
+    return True
+
+
+def _pm_feed_label(state: feeds.State) -> str:
+    status = "CONNECTED" if getattr(state, "pm_feed_connected", False) else "DISCONNECTED"
+    if getattr(state, "pm_feed_guard_active", False):
+        status += " | GUARD"
+    reason = getattr(state, "pm_last_refresh_reason", "") or getattr(state, "pm_last_error", "")
+    if reason:
+        status += f" | {reason}"
+    return status
+
+
+
 def get_strong_reasons(indicators):
     reasons = []
     if o := indicators.get("order_book_imbalance"):
@@ -280,6 +308,7 @@
                             f"📉 Polymarket DOWN: {pm_dn_str}\n"
                             f"🎯 Direction: {direction} (Score: {score})\n"
                             f"🧭 HTF Trend: {trend_direction}\n"
+                            f"📡 Feed: {_pm_feed_label(state)}\n"
                             f"⏱️ Time Left: {time_left} min"
                             f"{position_info}\n"
                             f"💰 Balance: ${status['balance']:.2f} | Trades: {status['trades_today']}/100 | PnL: ${status['total_pnl']:.2f}\n"
@@ -290,22 +319,26 @@
                         print(f"[ERROR] Neutral notification failed: {e}", flush=True)
 
                 trade_executed = False
-                try:
-                    trade = await paper_trader.check_signal(
-                        f"{coin}-{tf}",
-                        score,
-                        state.pm_up or 0,
-                        state.pm_dn or 0,
-                        candle_round,
-                        state.pm_up_id,
-                        state.pm_dn_id,
-                        trend_direction,
-                    )
-                    if trade:
-                        trade_executed = True
-                        print(f"[TRADE EXECUTED] {trade.get('side')} @ {trade.get('price',0):.4f}", flush=True)
-                except Exception as e:
-                    print(f"[ERROR] Trade execution failed: {e}", flush=True)
+                trade = None
+                if not _pm_feed_ok(state):
+                    print(f"[PM GUARD] Skip entry | {_pm_feed_label(state)}", flush=True)
+                else:
+                    try:
+                        trade = await paper_trader.check_signal(
+                            f"{coin}-{tf}",
+                            score,
+                            state.pm_up or 0,
+                            state.pm_dn or 0,
+                            candle_round,
+                            state.pm_up_id,
+                            state.pm_dn_id,
+                            trend_direction,
+                        )
+                        if trade:
+                            trade_executed = True
+                            print(f"[TRADE EXECUTED] {trade.get('side')} @ {trade.get('price',0):.4f}", flush=True)
+                    except Exception as e:
+                        print(f"[ERROR] Trade execution failed: {e}", flush=True)
 
                 if trade_executed and trade and TELEGRAM_ENABLED:
                     try:
@@ -408,7 +441,10 @@
     state = feeds.State()
     trend_state = feeds.State()
 
-    state.pm_up_id, state.pm_dn_id = feeds.fetch_pm_tokens(coin, tf)
+    if hasattr(feeds, "fetch_pm_tokens_robust"):
+        state.pm_up_id, state.pm_dn_id, state.pm_market_slug = feeds.fetch_pm_tokens_robust(coin, tf)
+    else:
+        state.pm_up_id, state.pm_dn_id = feeds.fetch_pm_tokens(coin, tf)
     if state.pm_up_id:
         console.print(f"  [PM] Up   → {state.pm_up_id[:24]}…")
         console.print(f"  [PM] Down → {state.pm_dn_id[:24]}…")
