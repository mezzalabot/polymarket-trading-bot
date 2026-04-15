# --- tuning konservatif ---
UP_SCORE_MIN = 70
UP_SCORE_MAX = 80

# --- di dalam check_signal / validasi entry UP ---
up_score_ok = ENABLE_UP_ENTRIES and UP_SCORE_MIN <= score <= UP_SCORE_MAX
up_price_ok = (pm_up_price >= min_entry and pm_up_price <= max_up)
up_trend_ok, up_trend_reason = check_trend_filter("UP", trend_direction)

if up_score_ok and up_price_ok and up_trend_ok:
    # enter UP
    pass
else:
    if ENABLE_UP_ENTRIES:
        if not up_score_ok:
            print(
                f"SKIP_UP | reason=SCORE | score={score} | "
                f"allowed={UP_SCORE_MIN}-{UP_SCORE_MAX}",
                flush=True,
            )
        elif not up_price_ok:
            if pm_up_price < min_entry:
                print(
                    f"SKIP_UP | reason=PRICE_TOO_LOW | up={pm_up_price:.4f} | "
                    f"min_entry={min_entry:.4f}",
                    flush=True,
                )
            else:
                print(
                    f"SKIP_UP | reason=PRICE_TOO_HIGH | up={pm_up_price:.4f} | "
                    f"max_up={max_up:.4f}",
                    flush=True,
                )
        elif not up_trend_ok:
            print(
                f"SKIP_UP | reason=TREND | trend={trend_direction} | "
                f"detail={up_trend_reason}",
                flush=True,
            )
