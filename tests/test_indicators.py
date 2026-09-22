from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finance_bot.indicators import _confirmed_pivots, compute_indicators, supertrend
from tests.conftest import random_walk_bars

_NUMERIC_CHECKED = [
    "ema20",
    "ema50",
    "ema200",
    "atr",
    "rsi",
    "macd",
    "macd_hist",
    "adx",
    "bb_upper",
    "bb_lower",
    "kc_upper",
    "donchian_high20",
    "stochrsi_k",
    "tenkan",
    "kijun",
    "senkou_a",
    "senkou_b",
    "supertrend",
    "supertrend_dir",
    "swing_high",
    "swing_low",
    "structure",
    "resistance",
    "support",
    "candle_pattern",
    "atr_pct_rank",
]


@pytest.mark.parametrize("cutoff", [320, 480, 555])
def test_every_indicator_is_point_in_time(cutoff: int) -> None:
    """Calcular con todo el historico o con el historico cortado en `cutoff`
    debe dar el MISMO valor en esa vela. Si no, el indicador mira al futuro."""
    bars = random_walk_bars(600, seed=4)
    full = compute_indicators(bars)
    truncated = compute_indicators(bars.iloc[: cutoff + 1])
    for col in _NUMERIC_CHECKED:
        a, b = full[col].iloc[cutoff], truncated[col].iloc[-1]
        if pd.isna(a) and pd.isna(b):
            continue
        assert a == pytest.approx(b, rel=1e-9, abs=1e-9), col


def test_swing_pivot_only_appears_after_confirmation() -> None:
    high = pd.Series([1, 2, 3, 10, 3, 2, 1, 1, 1], dtype=float)
    pivots = _confirmed_pivots(high, k=3, kind="high")
    # el maximo (10) esta en la posicion 3 pero no es "maximo de 7 velas" hasta ver 3 velas despues
    assert pivots.iloc[:6].isna().all()
    assert pivots.iloc[6] == 10


def test_rsi_bounded_and_supertrend_follows_trend() -> None:
    up = random_walk_bars(400, seed=2, drift=1.5)
    ind = compute_indicators(up)
    rsi = ind["rsi"].dropna()
    assert rsi.between(0, 100).all()
    _, direction = supertrend(up["high"], up["low"], up["close"])
    assert (direction.dropna().tail(100) == 1).mean() > 0.8


def test_atr_is_nan_during_warmup_not_zero() -> None:
    ind = compute_indicators(random_walk_bars(50, seed=3))
    assert ind["atr"].iloc[:10].isna().all()
    assert (ind["atr"].dropna() > 0).all()


def test_support_below_and_resistance_above_close() -> None:
    ind = compute_indicators(random_walk_bars(500, seed=9)).dropna(subset=["support", "resistance"])
    assert (ind["support"] < ind["close"]).all()
    assert (ind["resistance"] > ind["close"]).all()
    assert np.isfinite(ind[["support", "resistance"]].to_numpy()).all()
