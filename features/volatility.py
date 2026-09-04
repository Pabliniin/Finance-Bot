"""ATR, volatilidad realizada y clasificacion de regimen de mercado.

El regimen se calcula como el percentil del ATR actual DENTRO DE SU PROPIA
ventana movil (no contra todo el historico, que seria look-ahead: el bar 100
no puede saber que existira un pico de volatilidad en el bar 5000 y usarlo
como referencia de "normal").
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from ta.volatility import AverageTrueRange

_REGIME_LOW_MAX = 0.33
_REGIME_HIGH_MIN = 0.66


def add_volatility_features(
    df: pd.DataFrame,
    atr_window: int = 14,
    realized_vol_window: int = 20,
    regime_lookback: int = 100,
    bars_per_year: float = 252 * 24,  # aproximacion para H1; ajustar por timeframe si se usa en D1/H4
) -> pd.DataFrame:
    out = df.copy()

    atr = AverageTrueRange(out["high"], out["low"], out["close"], window=atr_window)
    out["atr"] = atr.average_true_range()

    log_returns = np.log(out["close"] / out["close"].shift(1))
    out["realized_vol"] = log_returns.rolling(realized_vol_window).std() * np.sqrt(bars_per_year)

    out["atr_percentile"] = out["atr"].rolling(regime_lookback).apply(_trailing_percentile, raw=True)
    out["vol_regime"] = out["atr_percentile"].apply(_bucket_regime)

    return out


def _trailing_percentile(window: np.ndarray) -> float:
    current = window[-1]
    if np.isnan(current):
        return np.nan
    valid = window[~np.isnan(window)]
    if len(valid) == 0:
        return np.nan
    return float((current > valid).mean())


def _bucket_regime(pct: float) -> str:
    if pd.isna(pct):
        return "unknown"
    if pct < _REGIME_LOW_MAX:
        return "low"
    if pct > _REGIME_HIGH_MIN:
        return "high"
    return "normal"
