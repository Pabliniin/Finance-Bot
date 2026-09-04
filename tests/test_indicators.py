from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.technical import add_technical_indicators
from features.volatility import add_volatility_features


def _synthetic_ohlcv(n: int = 300, seed: int = 42, trend: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    steps = rng.normal(loc=trend, scale=0.5, size=n)
    close = 100 + np.cumsum(steps)
    high = close + rng.uniform(0.05, 0.3, size=n)
    low = close - rng.uniform(0.05, 0.3, size=n)
    open_ = close + rng.normal(0, 0.1, size=n)
    volume = rng.uniform(50, 150, size=n)
    return pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_rsi_stays_within_bounds():
    df = add_technical_indicators(_synthetic_ohlcv())
    valid = df["rsi"].dropna()
    assert not valid.empty
    assert valid.between(0, 100).all()


def test_ema_fast_tracks_strong_uptrend_more_closely_than_ema_slow():
    df = add_technical_indicators(_synthetic_ohlcv(trend=0.4))
    tail = df.dropna(subset=["ema_fast", "ema_slow"]).tail(20)
    # en una tendencia alcista sostenida, la EMA rapida debe ir por encima de la lenta
    assert (tail["ema_fast"] > tail["ema_slow"]).mean() > 0.6


def test_add_technical_indicators_does_not_mutate_input():
    original = _synthetic_ohlcv()
    original_cols = list(original.columns)
    add_technical_indicators(original)
    assert list(original.columns) == original_cols


def test_atr_is_non_negative():
    df = add_volatility_features(_synthetic_ohlcv())
    valid = df["atr"].dropna()
    assert not valid.empty
    assert (valid >= 0).all()


def test_vol_regime_values_are_within_expected_categories():
    df = add_volatility_features(_synthetic_ohlcv())
    assert set(df["vol_regime"].unique()) <= {"low", "normal", "high", "unknown"}


@pytest.mark.parametrize("cutoff", [150, 220])
def test_technical_indicators_are_point_in_time_safe(cutoff):
    # Calcular sobre todo el historico y sobre un recorte hasta `cutoff` debe
    # dar el MISMO valor en la ultima barra del recorte: si difiriera,
    # significaria que el indicador esta usando datos posteriores a esa barra.
    full = _synthetic_ohlcv(n=300)
    truncated = full.iloc[: cutoff + 1].copy()

    full_result = add_technical_indicators(full)
    truncated_result = add_technical_indicators(truncated)

    full_row = full_result.iloc[cutoff]
    truncated_row = truncated_result.iloc[-1]

    for col in ["ema_fast", "ema_slow", "rsi", "macd", "adx"]:
        assert full_row[col] == pytest.approx(truncated_row[col], nan_ok=True)
