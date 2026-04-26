"""
Score Framework V2.1 - Bidirectional (UP + DOWN)
Implements hard filters + weighted scoring with microstructure priority
Supports both BULLISH (UP) and BEARISH (DOWN) entries
"""

import config
import indicators
from typing import Dict, Optional, Tuple

# Alias indicator functions — indicators.py exports obi(), vwap(), etc.
calc_obi = indicators.obi
calc_cvd = indicators.cvd
calc_walls = indicators.walls
calc_vwap = indicators.vwap
calc_rsi = indicators.rsi
calc_macd = indicators.macd


class ScoreV2:
    """
    V2.1 Scoring System with Hard Filters - Bidirectional
    
    Score Breakdown:
    - Microstructure: 35 pts (OBI, CVD, Walls)
    - Trend Context: 25 pts (EMA, VWAP, MACD)
    - Entry Timing: 25 pts (Pullback, HA, Candle Quality)
    - RSI Context: 5 pts (downgraded)
    - Market Quality: 10 pts (spread, depth)
    
    Total: 100 pts
    Entry threshold: 80+
    
    Direction is auto-detected from EMA alignment:
    - EMA fast > slow → BULLISH (UP entry)
    - EMA fast < slow → BEARISH (DOWN entry)
    """
    
    # Hard filter thresholds
    CHOP_THRESHOLD = 0.7
    LATE_ENTRY_THRESHOLD = 0.15  # % from VWAP
    OBI_PERSISTENCE_MIN = 3  # out of 5 snapshots
    
    # Minimum component scores
    MIN_MICROSTRUCTURE = 20
    MIN_ENTRY_TIMING = 15
    
    def __init__(self):
        self.obi_history = []
        self.wall_history = []
        
    def calculate_score(
        self,
        bids,
        asks,
        mid,
        trades,
        klines,
        spread: float = 0.0,
        depth_quality: float = 1.0,
    ) -> Tuple[float, Dict[str, any]]:
        """
        Calculate V2.1 score with hard filters - bidirectional
        
        Returns:
            (score, details_dict)
            score = 0 if hard filter fails
            details = breakdown of scoring including 'direction' key
        """
        details = {
            "hard_filters": {},
            "microstructure": 0,
            "trend_context": 0,
            "entry_timing": 0,
            "rsi_context": 0,
            "market_quality": 0,
            "penalties": 0,
            "total": 0,
            "passed_filters": True,
            "direction": "NEUTRAL",  # BULLISH or BEARISH
        }
        
        # ═══════════════════════════════════════════════════════
        # STEP 0: DETECT DIRECTION
        # ═══════════════════════════════════════════════════════
        
        es, el = indicators.emas(klines)
        
        if es is None or el is None:
            details["passed_filters"] = False
            details["fail_reason"] = "EMA data not available"
            return 0, details
        
        # Detect direction from EMA alignment
        if es > el:
            direction = "BULLISH"
        elif es < el:
            direction = "BEARISH"
        else:
            details["passed_filters"] = False
            details["fail_reason"] = "EMAs equal - no clear direction"
            return 0, details
        
        details["direction"] = direction
        is_bullish = direction == "BULLISH"
        
        # ═══════════════════════════════════════════════════════
        # STEP 1: HARD FILTERS (direction-aware)
        # ═══════════════════════════════════════════════════════
        
        # Filter 1: HTF Trend Align (auto-pass since direction = EMA alignment)
        details["hard_filters"]["htf_align"] = True
        
        # Filter 2: Chop Filter (EMA slope must be significant in either direction)
        if len(klines) >= 10:
            ema_slope = (es - klines[-10]["c"]) / klines[-10]["c"]
            # Bullish: slope should be positive; Bearish: slope should be negative
            if is_bullish:
                is_choppy = ema_slope < 0.001  # Need positive slope > 0.1%
            else:
                is_choppy = ema_slope > -0.001  # Need negative slope < -0.1%
            
            details["hard_filters"]["not_choppy"] = not is_choppy
            details["hard_filters"]["ema_slope"] = round(ema_slope, 6)
            
            if is_choppy:
                details["passed_filters"] = False
                details["fail_reason"] = f"Market too choppy (slope={ema_slope:.4%}, dir={direction})"
                return 0, details
        
        # Filter 3: Late Entry Filter (distance from VWAP)
        vwap_val = calc_vwap(klines)
        if vwap_val and mid:
            distance_from_vwap = abs(mid - vwap_val) / vwap_val
            not_late = distance_from_vwap < self.LATE_ENTRY_THRESHOLD
            details["hard_filters"]["not_late"] = not_late
            
            if not not_late:
                details["passed_filters"] = False
                details["fail_reason"] = f"Entry too late ({distance_from_vwap:.2%} from VWAP)"
                return 0, details
        
        # Filter 4: OBI Persistence (direction-aware)
        current_obi = calc_obi(bids, asks, mid) if mid else 0
        self.obi_history.append(current_obi)
        if len(self.obi_history) > 5:
            self.obi_history.pop(0)
        
        if len(self.obi_history) >= 3:
            if is_bullish:
                # Bullish: OBI should be positive (buy pressure)
                directional_count = sum(1 for x in self.obi_history if x > 0.2)
            else:
                # Bearish: OBI should be negative (sell pressure)
                directional_count = sum(1 for x in self.obi_history if x < -0.2)
            
            obi_persistent = directional_count >= self.OBI_PERSISTENCE_MIN
            details["hard_filters"]["obi_persistent"] = obi_persistent
            details["hard_filters"]["obi_directional_count"] = directional_count
            
            if not obi_persistent:
                details["passed_filters"] = False
                details["fail_reason"] = f"OBI not persistent for {direction} ({directional_count}/5)"
                return 0, details
        
        # Filter 5: Market Quality (spread check)
        if spread > 0:
            spread_ok = spread < 0.02  # < 2% spread
            details["hard_filters"]["spread_ok"] = spread_ok
            
            if not spread_ok:
                details["passed_filters"] = False
                details["fail_reason"] = f"Spread too wide ({spread:.2%})"
                return 0, details
        
        # ═══════════════════════════════════════════════════════
        # STEP 2: SCORING (direction-aware)
        # ═══════════════════════════════════════════════════════
        
        # A. MICROSTRUCTURE SCORE (35 pts)
        micro_score = 0
        
        # 1) OBI Persistent (15 pts)
        if is_bullish:
            if current_obi > 0.5:
                micro_score += 15  # Strong bullish
            elif current_obi > 0.2:
                micro_score += 8   # Weak bullish
        else:
            if current_obi < -0.5:
                micro_score += 15  # Strong bearish
            elif current_obi < -0.2:
                micro_score += 8   # Weak bearish
        
        # 2) CVD 5m (15 pts)
        cvd_val = calc_cvd(trades, 300)
        
        if is_bullish:
            if cvd_val > 0:
                if len(trades) > 100:
                    cvd_prev = calc_cvd(trades[:-50], 300)
                    if cvd_val > cvd_prev * 1.2:
                        micro_score += 15  # Strengthening buy
                    else:
                        micro_score += 8   # Positive but weak
        else:
            if cvd_val < 0:
                if len(trades) > 100:
                    cvd_prev = calc_cvd(trades[:-50], 300)
                    if cvd_val < cvd_prev * 1.2:  # More negative
                        micro_score += 15  # Strengthening sell
                    else:
                        micro_score += 8   # Negative but weak
        
        # 3) Walls Quality (5 pts)
        buy_walls, sell_walls = calc_walls(bids, asks)
        
        if is_bullish:
            wall_favorable = len(buy_walls) > len(sell_walls)
        else:
            wall_favorable = len(sell_walls) > len(buy_walls)
        
        self.wall_history.append(wall_favorable)
        if len(self.wall_history) > 5:
            self.wall_history.pop(0)
        
        if len(self.wall_history) >= 3:
            wall_persistent = sum(self.wall_history) >= 3
            if wall_persistent:
                micro_score += 5
            elif self.wall_history[-1]:
                micro_score += 2
        
        details["microstructure"] = micro_score
        
        # B. TREND CONTEXT SCORE (25 pts)
        trend_score = 0
        
        # 4) EMA Alignment strength (10 pts)
        if is_bullish:
            ema_slope_healthy = (es - klines[-5]["c"]) / klines[-5]["c"] > 0.002
        else:
            ema_slope_healthy = (es - klines[-5]["c"]) / klines[-5]["c"] < -0.002
        
        if ema_slope_healthy:
            trend_score += 10
        else:
            trend_score += 5
        
        # 5) VWAP Context (10 pts)
        if vwap_val and mid:
            if is_bullish:
                # Bullish: price above VWAP but not too extended
                if mid > vwap_val:
                    distance = (mid - vwap_val) / vwap_val
                    if distance < 0.05:
                        trend_score += 10
                    else:
                        trend_score += 5
            else:
                # Bearish: price below VWAP but not too extended
                if mid < vwap_val:
                    distance = (vwap_val - mid) / vwap_val
                    if distance < 0.05:
                        trend_score += 10
                    else:
                        trend_score += 5
        
        # 6) MACD Histogram (5 pts)
        _, _, macd_hist = calc_macd(klines)
        if macd_hist is not None:
            if is_bullish and macd_hist > 0:
                if len(klines) >= 2:
                    _, _, prev_hist = calc_macd(klines[:-1])
                    if prev_hist is not None and macd_hist > prev_hist:
                        trend_score += 5  # Growing bullish
                    else:
                        trend_score += 2
            elif not is_bullish and macd_hist < 0:
                if len(klines) >= 2:
                    _, _, prev_hist = calc_macd(klines[:-1])
                    if prev_hist is not None and macd_hist < prev_hist:
                        trend_score += 5  # Growing bearish
                    else:
                        trend_score += 2
        
        details["trend_context"] = trend_score
        
        # C. ENTRY TIMING SCORE (25 pts)
        timing_score = 0
        
        # 7) Pullback/Reclaim Quality (10 pts)
        if len(klines) >= 5:
            recent_low = min(k["l"] for k in klines[-5:])
            recent_high = max(k["h"] for k in klines[-5:])
            current_close = klines[-1]["c"]
            price_range = recent_high - recent_low
            
            if price_range > 0:
                if is_bullish:
                    # Bullish: price near recent high after pullback
                    reclaim_pct = (current_close - recent_low) / price_range
                    if reclaim_pct > 0.7:
                        timing_score += 10
                    elif reclaim_pct > 0.5:
                        timing_score += 5
                else:
                    # Bearish: price near recent low after bounce rejection
                    drop_pct = (recent_high - current_close) / price_range
                    if drop_pct > 0.7:
                        timing_score += 10
                    elif drop_pct > 0.5:
                        timing_score += 5
        
        # 8) Heikin-Ashi Structure (5 pts)
        ha = indicators.heikin_ashi(klines)
        if ha and len(ha) >= 3:
            recent_ha = ha[-3:]
            if is_bullish:
                all_directional = all(c["green"] for c in recent_ha)
                last_directional = recent_ha[-1]["green"]
            else:
                all_directional = all(not c["green"] for c in recent_ha)
                last_directional = not recent_ha[-1]["green"]
            
            if all_directional:
                timing_score += 5
            elif last_directional:
                timing_score += 2
        
        # 9) Candle Quality (10 pts)
        last_candle = klines[-1]
        body = abs(last_candle["c"] - last_candle["o"])
        full_range = last_candle["h"] - last_candle["l"]
        
        if full_range > 0:
            body_ratio = body / full_range
            # Check candle direction matches our direction
            candle_bullish = last_candle["c"] > last_candle["o"]
            candle_matches = candle_bullish if is_bullish else not candle_bullish
            
            if candle_matches and 0.5 <= body_ratio <= 0.8:
                timing_score += 10
            elif candle_matches and 0.3 <= body_ratio <= 0.9:
                timing_score += 5
            elif 0.3 <= body_ratio <= 0.9:
                timing_score += 2  # Good structure but wrong direction
        
        details["entry_timing"] = timing_score
        
        # D. RSI CONTEXT SCORE (5 pts) - Downgraded
        rsi_val = calc_rsi(klines)
        rsi_score = 0
        
        if rsi_val is not None:
            if is_bullish:
                if 50 <= rsi_val <= 65:
                    rsi_score = 5  # Healthy bullish continuation
                elif 65 < rsi_val <= 72:
                    rsi_score = 2  # Getting extended
            else:
                if 35 <= rsi_val <= 50:
                    rsi_score = 5  # Healthy bearish continuation
                elif 28 < rsi_val < 35:
                    rsi_score = 2  # Getting oversold
        
        details["rsi_context"] = rsi_score
        
        # E. MARKET QUALITY BONUS (10 pts)
        quality_score = 0
        
        if spread > 0 and spread < 0.005:
            quality_score += 5
        elif spread > 0 and spread < 0.01:
            quality_score += 2
        
        if depth_quality > 0.8:
            quality_score += 5
        elif depth_quality > 0.5:
            quality_score += 3
        
        details["market_quality"] = quality_score
        
        # ═══════════════════════════════════════════════════════
        # PENALTIES (direction-aware)
        # ═══════════════════════════════════════════════════════
        
        penalties = 0
        
        # Penalty 1: Too far from VWAP
        if vwap_val and mid:
            vwap_distance = abs(mid - vwap_val) / vwap_val
            if vwap_distance > 0.10:
                penalties += 5
                details["penalty_vwap_distance"] = True
        
        # Penalty 2: Exhaustion risk (3 consecutive candles in our direction)
        if len(klines) >= 3:
            last_3 = klines[-3:]
            if is_bullish:
                all_directional = all(k["c"] > k["o"] for k in last_3)
            else:
                all_directional = all(k["c"] < k["o"] for k in last_3)
            
            if all_directional:
                penalties += 5
                details["penalty_exhaustion"] = True
        
        # Penalty 3: Walls not stable
        if len(self.wall_history) >= 5:
            wall_changes = sum(1 for i in range(1, len(self.wall_history)) 
                             if self.wall_history[i] != self.wall_history[i-1])
            if wall_changes >= 3:
                penalties += 5
                details["penalty_unstable_walls"] = True
        
        details["penalties"] = penalties
        
        # ═══════════════════════════════════════════════════════
        # FINAL SCORE
        # ═══════════════════════════════════════════════════════
        
        total_score = micro_score + trend_score + timing_score + rsi_score + quality_score - penalties
        total_score = max(0, min(100, total_score))
        
        details["total"] = total_score
        
        # Apply minimum component rules
        if micro_score < self.MIN_MICROSTRUCTURE:
            details["fail_reason"] = f"Microstructure too weak ({micro_score}/{self.MIN_MICROSTRUCTURE})"
            return 0, details
        
        if timing_score < self.MIN_ENTRY_TIMING:
            details["fail_reason"] = f"Entry timing too weak ({timing_score}/{self.MIN_ENTRY_TIMING})"
            return 0, details
        
        return total_score, details
    
    def reset_history(self):
        """Reset OBI and wall history (e.g., after trade or market change)"""
        self.obi_history = []
        self.wall_history = []


# Global instance
scorer_v2 = ScoreV2()


def bias_score_v2(bids, asks, mid, trades, klines, spread=0.0, depth_quality=1.0) -> Tuple[float, Dict]:
    """
    V2.1 scoring function - drop-in replacement for bias_score
    
    Returns:
        (score, details)
        score: 0-100 (0 if hard filter fails)
        details: breakdown dict with 'direction' key (BULLISH/BEARISH/NEUTRAL)
    """
    return scorer_v2.calculate_score(bids, asks, mid, trades, klines, spread, depth_quality)
