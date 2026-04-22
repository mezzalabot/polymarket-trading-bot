"""
Score Framework V2 - Cleaner, More Selective
Implements hard filters + weighted scoring with microstructure priority
"""

import config
import indicators
from typing import Dict, Optional, Tuple


class ScoreV2:
    """
    V2 Scoring System with Hard Filters
    
    Score Breakdown:
    - Microstructure: 35 pts (OBI, CVD, Walls)
    - Trend Context: 25 pts (EMA, VWAP, MACD)
    - Entry Timing: 25 pts (Pullback, HA, Candle Quality)
    - RSI Context: 5 pts (downgraded)
    - Market Quality: 10 pts (spread, depth)
    
    Total: 100 pts
    Entry threshold: 80+
    """
    
    # Hard filter thresholds
    CHOP_THRESHOLD = 0.7  # ADX or similar
    LATE_ENTRY_THRESHOLD = 0.15  # % from VWAP
    OBI_PERSISTENCE_MIN = 3  # out of 5 snapshots
    
    # Minimum component scores
    MIN_MICROSTRUCTURE = 20
    MIN_ENTRY_TIMING = 15
    
    def __init__(self):
        self.obi_history = []  # Track last 5 OBI values
        self.wall_history = []  # Track last 5 wall states
        
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
        Calculate V2 score with hard filters
        
        Returns:
            (score, details_dict)
            score = 0 if hard filter fails
            details = breakdown of scoring
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
        }
        
        # ═══════════════════════════════════════════════════════
        # STEP 1: HARD FILTERS
        # ═══════════════════════════════════════════════════════
        
        # Filter 1: HTF Trend Align
        es, el = indicators.emas(klines)
        htf_align = es is not None and el is not None and es > el
        details["hard_filters"]["htf_align"] = htf_align
        
        if not htf_align:
            details["passed_filters"] = False
            details["fail_reason"] = "HTF trend not aligned"
            return 0, details
        
        # Filter 2: Chop Filter (simplified - check EMA slope)
        if len(klines) >= 10:
            ema_slope = (es - klines[-10]["c"]) / klines[-10]["c"]
            is_choppy = abs(ema_slope) < 0.001  # < 0.1% slope
            details["hard_filters"]["not_choppy"] = not is_choppy
            
            if is_choppy:
                details["passed_filters"] = False
                details["fail_reason"] = "Market too choppy"
                return 0, details
        
        # Filter 3: Late Entry Filter
        vwap_val = indicators.vwap
        vwap_val = calc_vwap(klines)
        if vwap_val and mid:
            distance_from_vwap = abs(mid - vwap_val) / vwap_val
            not_late = distance_from_vwap < self.LATE_ENTRY_THRESHOLD
            details["hard_filters"]["not_late"] = not_late
            
            if not not_late:
                details["passed_filters"] = False
                details["fail_reason"] = f"Entry too late ({distance_from_vwap:.2%} from VWAP)"
                return 0, details
        
        # Filter 4: OBI Persistence
        obi_val = indicators.obi
        current_obi = calc_obi(bids, asks, mid) if mid else 0
        self.obi_history.append(current_obi)
        if len(self.obi_history) > 5:
            self.obi_history.pop(0)
        
        if len(self.obi_history) >= 3:
            bullish_count = sum(1 for x in self.obi_history if x > 0.2)
            obi_persistent = bullish_count >= self.OBI_PERSISTENCE_MIN
            details["hard_filters"]["obi_persistent"] = obi_persistent
            
            if not obi_persistent:
                details["passed_filters"] = False
                details["fail_reason"] = f"OBI not persistent ({bullish_count}/5 bullish)"
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
        # STEP 2: SCORING (only if passed all filters)
        # ═══════════════════════════════════════════════════════
        
        # A. MICROSTRUCTURE SCORE (35 pts)
        micro_score = 0
        
        # 1) OBI Persistent (15 pts)
        if current_obi > 0.5:
            micro_score += 15  # Strong bullish
        elif current_obi > 0.2:
            micro_score += 8   # Weak bullish
        
        # 2) CVD 5m (15 pts)
        cvd_val = indicators.cvd
        cvd_val = calc_cvd(trades, 300)
        if cvd_val > 0:
            # Check if CVD is strengthening
            if len(trades) > 100:
                cvd_prev = calc_cvd(trades[:-50], 300)
                if cvd_val > cvd_prev * 1.2:
                    micro_score += 15  # Strengthening
                else:
                    micro_score += 8   # Positive but weak
        
        # 3) Walls Quality (5 pts)
        walls_val = indicators.walls
        buy_walls, sell_walls = calc_walls(bids, asks)
        
        self.wall_history.append(len(buy_walls) > len(sell_walls))
        if len(self.wall_history) > 5:
            self.wall_history.pop(0)
        
        if len(self.wall_history) >= 3:
            wall_persistent = sum(self.wall_history) >= 3
            if wall_persistent and len(buy_walls) > 0:
                micro_score += 5
            elif len(buy_walls) > 0:
                micro_score += 2
        
        details["microstructure"] = micro_score
        
        # B. TREND CONTEXT SCORE (25 pts)
        trend_score = 0
        
        # 4) EMA Alignment (10 pts)
        if es > el:
            ema_slope_healthy = (es - klines[-5]["c"]) / klines[-5]["c"] > 0.002
            if ema_slope_healthy:
                trend_score += 10
            else:
                trend_score += 5
        
        # 5) VWAP Context (10 pts)
        if vwap_val and mid:
            if mid > vwap_val:
                distance = (mid - vwap_val) / vwap_val
                if distance < 0.05:  # Not too extended
                    trend_score += 10
                else:
                    trend_score += 5
        
        # 6) MACD Histogram (5 pts)
        macd_val = indicators.macd
        _, _, macd_hist = calc_macd(klines)
        if macd_hist is not None and macd_hist > 0:
            if len(klines) >= 2:
                _, _, prev_hist = calc_macd(klines[:-1])
                if prev_hist is not None and macd_hist > prev_hist:
                    trend_score += 5  # Growing
                else:
                    trend_score += 2  # Positive but flat
        
        details["trend_context"] = trend_score
        
        # C. ENTRY TIMING SCORE (25 pts)
        timing_score = 0
        
        # 7) Pullback/Reclaim Quality (10 pts)
        # Check if price recently pulled back and reclaimed
        if len(klines) >= 5:
            recent_low = min(k["l"] for k in klines[-5:])
            recent_high = max(k["h"] for k in klines[-5:])
            current_close = klines[-1]["c"]
            
            # Good reclaim: price near recent high after pullback
            if current_close > (recent_low + (recent_high - recent_low) * 0.7):
                timing_score += 10
            elif current_close > (recent_low + (recent_high - recent_low) * 0.5):
                timing_score += 5
        
        # 8) Heikin-Ashi Structure (5 pts)
        ha = indicators.heikin_ashi
        ha = heikin_ashi(klines)
        if ha and len(ha) >= 3:
            recent_ha = ha[-3:]
            all_green = all(c["green"] for c in recent_ha)
            if all_green:
                timing_score += 5
            elif recent_ha[-1]["green"]:
                timing_score += 2
        
        # 9) Candle Quality (10 pts)
        last_candle = klines[-1]
        body = abs(last_candle["c"] - last_candle["o"])
        full_range = last_candle["h"] - last_candle["l"]
        
        if full_range > 0:
            body_ratio = body / full_range
            # Good candle: body 50-80% of range
            if 0.5 <= body_ratio <= 0.8:
                timing_score += 10
            elif 0.3 <= body_ratio <= 0.9:
                timing_score += 5
            # Spike or doji: 0 pts
        
        details["entry_timing"] = timing_score
        
        # D. RSI CONTEXT SCORE (5 pts) - Downgraded
        rsi_val = indicators.rsi
        rsi_val = calc_rsi(klines)
        rsi_score = 0
        
        if rsi_val is not None:
            if 50 <= rsi_val <= 65:
                rsi_score = 5  # Healthy continuation zone
            elif 65 < rsi_val <= 72:
                rsi_score = 2  # Getting extended
            # <50 or >72: 0 pts
        
        details["rsi_context"] = rsi_score
        
        # E. MARKET QUALITY BONUS (10 pts)
        quality_score = 0
        
        if spread > 0 and spread < 0.005:  # < 0.5% spread
            quality_score += 5
        elif spread > 0 and spread < 0.01:
            quality_score += 2
        
        if depth_quality > 0.8:
            quality_score += 5
        elif depth_quality > 0.5:
            quality_score += 3
        
        details["market_quality"] = quality_score
        
        # ═══════════════════════════════════════════════════════
        # PENALTIES
        # ═══════════════════════════════════════════════════════
        
        penalties = 0
        
        # Penalty 1: Too far from VWAP
        if vwap_val and mid and (mid - vwap_val) / vwap_val > 0.10:
            penalties += 5
            details["penalty_vwap_distance"] = True
        
        # Penalty 2: 3 green candles in a row (exhaustion risk)
        if len(klines) >= 3:
            last_3 = klines[-3:]
            all_green = all(k["c"] > k["o"] for k in last_3)
            if all_green:
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
    V2 scoring function - drop-in replacement for bias_score
    
    Returns:
        (score, details)
        score: 0-100 (0 if hard filter fails)
        details: breakdown dict
    """
    return scorer_v2.calculate_score(bids, asks, mid, trades, klines, spread, depth_quality)
