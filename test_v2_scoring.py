#!/usr/bin/env python3
"""
Quick test of V2 scoring system
"""
import sys
sys.path.insert(0, 'src')

from scoring_v2 import bias_score_v2

# Mock data for testing
mock_bids = [[0.45, 1000], [0.44, 500]]
mock_asks = [[0.46, 800], [0.47, 400]]
mock_mid = 0.455
mock_trades = []
mock_klines = [
    {"o": 0.44, "h": 0.46, "l": 0.43, "c": 0.45, "v": 1000},
    {"o": 0.45, "h": 0.47, "l": 0.44, "c": 0.46, "v": 1200},
    {"o": 0.46, "h": 0.48, "l": 0.45, "c": 0.47, "v": 1100},
    {"o": 0.47, "h": 0.49, "l": 0.46, "c": 0.48, "v": 1300},
    {"o": 0.48, "h": 0.50, "l": 0.47, "c": 0.49, "v": 1400},
]

print("Testing V2 Scoring System...")
print("=" * 60)

try:
    score, details = bias_score_v2(
        mock_bids,
        mock_asks,
        mock_mid,
        mock_trades,
        mock_klines,
        spread=0.01,
        depth_quality=0.9
    )
    
    print(f"\n✅ V2 Scoring Test PASSED")
    print(f"\nFinal Score: {score}/100")
    print(f"Passed Filters: {details['passed_filters']}")
    
    if not details['passed_filters']:
        print(f"Fail Reason: {details.get('fail_reason', 'Unknown')}")
    
    print(f"\nScore Breakdown:")
    print(f"  - Microstructure: {details['microstructure']}/35")
    print(f"  - Trend Context: {details['trend_context']}/25")
    print(f"  - Entry Timing: {details['entry_timing']}/25")
    print(f"  - RSI Context: {details['rsi_context']}/5")
    print(f"  - Market Quality: {details['market_quality']}/10")
    print(f"  - Penalties: -{details['penalties']}")
    
    print(f"\nHard Filters:")
    for filter_name, passed in details['hard_filters'].items():
        status = "✅" if passed else "❌"
        print(f"  {status} {filter_name}")
    
    print("\n" + "=" * 60)
    print("V2 Implementation Ready for Tahap 2! 🚀")
    
except Exception as e:
    print(f"\n❌ V2 Scoring Test FAILED")
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
