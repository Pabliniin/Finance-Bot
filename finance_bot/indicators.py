"""Indicadores tecnicos sobre velas ya cerradas.

Los estandar (EMA, RSI, MACD, ADX/DMI, ATR, Bollinger, Keltner, Stoch RSI,
Ichimoku) vienen de la libreria `ta` (testeada; requisito del proyecto: nada
de indicadores clasicos "a mano"). Solo se implementan aqui los que `ta` no
trae: Supertrend, pivotes de swing confirmados, soportes/resistencias y
patrones de vela, todos con tests propios.

Regla point-in-time: cada columna en la fila t usa exclusivamente velas <= t.
Los pivotes de swing son la trampa clasica (un maximo "de 7 velas" necesita
3 velas posteriores para saberse maximo): aqui un pivote solo aparece en la
fila en la que queda CONFIRMADO, nunca en la vela del extremo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, StochRSIIndicator
from ta.trend import MACD, ADXIndicator, EMAIndicator, IchimokuIndicator
from ta.volatility import AverageTrueRange, BollingerBands, KeltnerChannel

PIVOT_K = 3  # un pivote es el extremo de 2*K+1 velas
SR_LOOKBACK_PIVOTS = 12  # pivotes recientes considerados como soportes/resistencias


def compute_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Devuelve una copia de `bars` con todas las columnas de indicadores."""
    out = bars.copy()
    high, low, close, open_ = out["high"], out["low"], out["close"], out["open"]

    out["ema20"] = EMAIndicator(close, window=20).ema_indicator()
    out["ema50"] = EMAIndicator(close, window=50).ema_indicator()
    out["ema200"] = EMAIndicator(close, window=200).ema_indicator()

    atr = AverageTrueRange(high, low, close, window=14).average_true_range()
    atr = atr.where(atr > 0)  # `ta` devuelve 0 durante el calentamiento: eso no es "volatilidad cero"
    out["atr"] = atr
    out["atr_pct_rank"] = atr.rolling(200, min_periods=50).rank(pct=True)
    out["ema200_slope_atr"] = (out["ema200"] - out["ema200"].shift(20)) / atr

    out["rsi"] = RSIIndicator(close, window=14).rsi()

    macd = MACD(close, window_fast=12, window_slow=26, window_sign=9)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()

    adx = ADXIndicator(high, low, close, window=14)
    out["adx"] = adx.adx().where(lambda s: s > 0)
    out["di_plus"] = adx.adx_pos()
    out["di_minus"] = adx.adx_neg()

    bb = BollingerBands(close, window=20, window_dev=2)
    out["bb_upper"] = bb.bollinger_hband()
    out["bb_lower"] = bb.bollinger_lband()
    out["bb_mid"] = bb.bollinger_mavg()

    kc = KeltnerChannel(high, low, close, window=20, window_atr=20, original_version=False, multiplier=1.5)
    out["kc_upper"] = kc.keltner_channel_hband()
    out["kc_lower"] = kc.keltner_channel_lband()
    out["squeeze_on"] = (out["bb_upper"] < out["kc_upper"]) & (out["bb_lower"] > out["kc_lower"])

    # Donchian excluyendo la vela actual: "rompe el maximo de las 20 anteriores"
    out["donchian_high20"] = high.rolling(20).max().shift(1)
    out["donchian_low20"] = low.rolling(20).min().shift(1)
    out["donchian_high55"] = high.rolling(55).max().shift(1)
    out["donchian_low55"] = low.rolling(55).min().shift(1)

    stoch = StochRSIIndicator(close, window=14, smooth1=3, smooth2=3)
    out["stochrsi_k"] = stoch.stochrsi_k() * 100
    out["stochrsi_d"] = stoch.stochrsi_d() * 100

    ichi = IchimokuIndicator(high, low, window1=9, window2=26, window3=52, visual=False)
    out["tenkan"] = ichi.ichimoku_conversion_line()
    out["kijun"] = ichi.ichimoku_base_line()
    # La nube "de hoy" se calculo hace 26 velas: desplazar hacia atras es point-in-time.
    out["senkou_a"] = ichi.ichimoku_a().shift(26)
    out["senkou_b"] = ichi.ichimoku_b().shift(26)

    out["roc10_atr"] = (close - close.shift(10)) / atr

    st_line, st_dir = supertrend(high, low, close, period=10, multiplier=3.0)
    out["supertrend"] = st_line
    out["supertrend_dir"] = st_dir

    out = out.join(swing_structure(high, low, close, k=PIVOT_K))
    out = out.join(support_resistance(high, low, close, k=PIVOT_K, lookback=SR_LOOKBACK_PIVOTS))
    out = out.join(candle_patterns(open_, high, low, close))
    return out


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 10, multiplier: float = 3.0
) -> tuple[pd.Series, pd.Series]:
    """Supertrend clasico (bandas ATR con trailing). Devuelve (linea, direccion
    +1/-1). Iterativo por construccion: cada banda depende de la anterior."""
    atr = AverageTrueRange(high, low, close, window=period).average_true_range().to_numpy()
    hl2 = ((high + low) / 2).to_numpy()
    c = close.to_numpy()
    n = len(c)
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    line = np.full(n, np.nan)
    direction = np.zeros(n)

    start = period  # antes, el ATR de `ta` aun no es valido
    for i in range(start, n):
        if i == start or np.isnan(final_upper[i - 1]):
            final_upper[i], final_lower[i] = upper[i], lower[i]
            direction[i] = 1 if c[i] >= hl2[i] else -1
        else:
            final_upper[i] = (
                upper[i] if (upper[i] < final_upper[i - 1] or c[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
            )
            final_lower[i] = (
                lower[i] if (lower[i] > final_lower[i - 1] or c[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
            )
            if direction[i - 1] == 1:
                direction[i] = -1 if c[i] < final_lower[i] else 1
            else:
                direction[i] = 1 if c[i] > final_upper[i] else -1
        line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    direction_series = pd.Series(direction, index=close.index).replace(0, np.nan)
    return pd.Series(line, index=close.index), direction_series


def _confirmed_pivots(series: pd.Series, k: int, kind: str) -> pd.Series:
    """Valor del pivote en la fila donde queda confirmado (k velas despues del
    extremo); NaN en el resto. Empates: vale el primero."""
    window = 2 * k + 1
    rolled = series.rolling(window).max() if kind == "high" else series.rolling(window).min()
    candidate = series.shift(k)
    is_pivot = candidate == rolled
    return candidate.where(is_pivot)


def swing_structure(high: pd.Series, low: pd.Series, close: pd.Series, k: int = PIVOT_K) -> pd.DataFrame:
    ph = _confirmed_pivots(high, k, "high")
    pl = _confirmed_pivots(low, k, "low")

    last_high = ph.ffill()
    last_low = pl.ffill()
    prev_high = ph.dropna().shift(1).reindex(ph.index).ffill()
    prev_low = pl.dropna().shift(1).reindex(pl.index).ffill()

    structure = pd.Series(0.0, index=close.index)
    structure[(last_high > prev_high) & (last_low > prev_low)] = 1.0  # maximos y minimos crecientes
    structure[(last_high < prev_high) & (last_low < prev_low)] = -1.0  # maximos y minimos decrecientes

    leg = last_high - last_low
    retracement_long = ((last_high - close) / leg).where(leg > 0)
    retracement_short = ((close - last_low) / leg).where(leg > 0)

    return pd.DataFrame(
        {
            "swing_high": last_high,
            "swing_low": last_low,
            "structure": structure,
            "retrace_long": retracement_long,
            "retrace_short": retracement_short,
        },
        index=close.index,
    )


def support_resistance(
    high: pd.Series, low: pd.Series, close: pd.Series, k: int = PIVOT_K, lookback: int = SR_LOOKBACK_PIVOTS
) -> pd.DataFrame:
    """Resistencia mas cercana POR ENCIMA y soporte mas cercano POR DEBAJO del
    cierre, entre los `lookback` ultimos pivotes confirmados (de maximos y de
    minimos: un soporte roto pasa a ser resistencia y viceversa)."""
    ph = _confirmed_pivots(high, k, "high")
    pl = _confirmed_pivots(low, k, "low")
    pivots = pd.concat([ph.dropna(), pl.dropna()]).sort_index(kind="stable")
    n = len(close)
    if pivots.empty:
        nan = pd.Series(np.nan, index=close.index)
        return pd.DataFrame({"resistance": nan, "support": nan}, index=close.index)

    positions = close.index.get_indexer(pivots.index)
    values = pivots.to_numpy()
    order = np.argsort(positions, kind="stable")
    positions, values = positions[order], values[order]

    counts = np.searchsorted(positions, np.arange(n), side="right")  # pivotes confirmados hasta cada fila
    offsets = np.arange(lookback)
    idx = counts[:, None] - 1 - offsets[None, :]
    valid = idx >= 0
    window_values = np.where(valid, values[np.clip(idx, 0, None)], np.nan)

    c = close.to_numpy()[:, None]
    above = np.where(window_values > c, window_values, np.nan)
    below = np.where(window_values < c, window_values, np.nan)
    with np.errstate(all="ignore"):
        resistance = np.nanmin(np.where(np.isnan(above), np.inf, above), axis=1)
        support = np.nanmax(np.where(np.isnan(below), -np.inf, below), axis=1)
    resistance = np.where(np.isinf(resistance), np.nan, resistance)
    support = np.where(np.isinf(support), np.nan, support)
    return pd.DataFrame({"resistance": resistance, "support": support}, index=close.index)


def candle_patterns(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
    body = (close - open_).abs()
    rng = (high - low).replace(0, np.nan)
    upper_wick = high - np.maximum(open_, close)
    lower_wick = np.minimum(open_, close) - low
    prev_open, prev_close = open_.shift(1), close.shift(1)

    bull_engulf = (prev_close < prev_open) & (close > open_) & (close >= prev_open) & (open_ <= prev_close)
    bear_engulf = (prev_close > prev_open) & (close < open_) & (close <= prev_open) & (open_ >= prev_close)
    hammer = (lower_wick >= 2 * body) & (upper_wick <= 0.3 * rng) & (body / rng <= 0.4)
    shooting_star = (upper_wick >= 2 * body) & (lower_wick <= 0.3 * rng) & (body / rng <= 0.4)

    pattern = pd.Series(0.0, index=close.index)
    pattern[bull_engulf | hammer] = 1.0
    pattern[bear_engulf | shooting_star] = -1.0
    pattern[(bull_engulf | hammer) & (bear_engulf | shooting_star)] = 0.0
    return pd.DataFrame({"candle_pattern": pattern}, index=close.index)
