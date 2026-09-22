from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finance_bot.research.evaluation import (
    benjamini_hochberg,
    non_overlapping,
    random_baseline_pvalue,
    summarize,
    wilson_interval,
)


def _trades(times: list[str], exits: list[str], rs: list[float], group=("XAUUSD", "H1")) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close_time": pd.to_datetime(times, utc=True),
            "exit_time": pd.to_datetime(exits, utc=True),
            "symbol": group[0],
            "tf": group[1],
            "realized_r": rs,
            "hit_tp1": [float(r > 0) for r in rs],
            "hit_tp2": 0.0,
            "hours_to_exit": 5.0,
        }
    )


def test_non_overlapping_skips_signals_while_a_trade_is_open() -> None:
    t = _trades(
        ["2024-01-01 10:00", "2024-01-01 11:00", "2024-01-01 16:00"],
        ["2024-01-01 15:00", "2024-01-01 12:00", "2024-01-01 18:00"],
        [1.0, 1.0, -1.0],
    )
    kept = non_overlapping(t)
    assert list(kept["realized_r"]) == [1.0, -1.0]  # la de las 11:00 cae dentro de la primera


def test_non_overlapping_is_per_group() -> None:
    a = _trades(["2024-01-01 10:00"], ["2024-01-02 10:00"], [1.0], ("XAUUSD", "H1"))
    b = _trades(["2024-01-01 11:00"], ["2024-01-01 12:00"], [1.0], ("EURUSD", "H1"))
    assert len(non_overlapping(pd.concat([a, b]))) == 2


def test_benjamini_hochberg_known_example() -> None:
    p = {"a": 0.001, "b": 0.008, "c": 0.039, "d": 0.041, "e": 0.27}
    result = benjamini_hochberg(p, alpha=0.05)
    assert result == {"a": True, "b": True, "c": False, "d": False, "e": False}


def test_wilson_interval_contains_rate() -> None:
    lo, hi = wilson_interval(60, 100)
    assert lo < 0.6 < hi and lo > 0.49 and hi < 0.70


def test_summary_drawdown_and_pvalue() -> None:
    t = _trades(["2024-01-01"] * 4, ["2024-01-02"] * 4, [1.0, -1.0, -1.0, 2.0])
    t["close_time"] = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    s = summarize(t)
    assert s.n == 4 and s.total_r == pytest.approx(1.0)
    assert s.max_drawdown_r == pytest.approx(2.0)
    assert 0 < s.p_value_vs_zero < 1


def test_random_baseline_pvalue_detects_better_than_random() -> None:
    rng = np.random.default_rng(0)
    pool = rng.normal(-0.05, 1.0, size=5000)
    p, mean_random = random_baseline_pvalue(0.5, 100, pool, iterations=300)
    assert p < 0.01 and mean_random == pytest.approx(-0.05, abs=0.03)
