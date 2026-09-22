"""Estrategias de ENTRADA (setups). Un trigger dice "aqui hay un momento
concreto para entrar"; los votantes (voters.py) dicen "cuanto respalda el
contexto esa entrada". Un candidato nace cuando al menos un trigger dispara
en una vela; despues el modelo calibrado decide si su probabilidad y su
expectativa justifican emitir la señal.

Todos son estrategias clasicas y publicas, con parametros FIJOS iguales para
ambos instrumentos y todas las temporalidades (sin optimizacion por
instrumento: esa es la receta del sobreajuste).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Trigger:
    key: str
    name: str
    long: Callable[[pd.DataFrame], pd.Series]
    short: Callable[[pd.DataFrame], pd.Series]


def _b(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(bool)


def _crossed_above(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return _b((a > b) & (a.shift(1) <= b_prev))


def _crossed_below(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return _b((a < b) & (a.shift(1) >= b_prev))


# 1. Pullback a la EMA20/50 en tendencia
def _pullback_long(f: pd.DataFrame) -> pd.Series:
    trend = (f.ema50 > f.ema200) & (f.close > f.ema200)
    touched = f.low.rolling(3).min() <= f.ema20
    return _b(trend & touched & (f.close > f.ema20) & (f.close > f.open))


def _pullback_short(f: pd.DataFrame) -> pd.Series:
    trend = (f.ema50 < f.ema200) & (f.close < f.ema200)
    touched = f.high.rolling(3).max() >= f.ema20
    return _b(trend & touched & (f.close < f.ema20) & (f.close < f.open))


# 2. Ruptura de canal Donchian 20 con ADX creciente (tortugas)
def _breakout_long(f: pd.DataFrame) -> pd.Series:
    return _b((f.close > f.donchian_high20) & (f.adx >= 18) & (f.adx > f.adx.shift(3)))


def _breakout_short(f: pd.DataFrame) -> pd.Series:
    return _b((f.close < f.donchian_low20) & (f.adx >= 18) & (f.adx > f.adx.shift(3)))


# 3. Cruce de MACD a favor de la tendencia de fondo
def _macd_long(f: pd.DataFrame) -> pd.Series:
    return _crossed_above(f.macd, f.macd_signal) & _b(f.close > f.ema200)


def _macd_short(f: pd.DataFrame) -> pd.Series:
    return _crossed_below(f.macd, f.macd_signal) & _b(f.close < f.ema200)


# 4. Pullback de RSI en tendencia (el RSI recupera 40 / pierde 60)
def _rsi_long(f: pd.DataFrame) -> pd.Series:
    return _crossed_above(f.rsi, 40.0) & _b(f.close > f.ema200)


def _rsi_short(f: pd.DataFrame) -> pd.Series:
    return _crossed_below(f.rsi, 60.0) & _b(f.close < f.ema200)


# 5. Reversion en Bollinger en mercado lateral (ADX < 20)
def _bollinger_long(f: pd.DataFrame) -> pd.Series:
    return _crossed_above(f.close, f.bb_lower) & _b(f.adx < 20)


def _bollinger_short(f: pd.DataFrame) -> pd.Series:
    return _crossed_below(f.close, f.bb_upper) & _b(f.adx < 20)


# 6. Ruptura de estructura (cierre sobre el ultimo maximo de swing confirmado)
def _bos_long(f: pd.DataFrame) -> pd.Series:
    return _crossed_above(f.close, f.swing_high) & _b(f.ema50 > f.ema200)


def _bos_short(f: pd.DataFrame) -> pd.Series:
    return _crossed_below(f.close, f.swing_low) & _b(f.ema50 < f.ema200)


# 7. Salida de compresion (squeeze) con momentum
def _squeeze_long(f: pd.DataFrame) -> pd.Series:
    was_squeezed = f.squeeze_on.astype(float).shift(1).rolling(8).sum() >= 6
    return _b(was_squeezed & ~f.squeeze_on.astype(bool) & (f.close > f.bb_mid) & (f.macd_hist > 0))


def _squeeze_short(f: pd.DataFrame) -> pd.Series:
    was_squeezed = f.squeeze_on.astype(float).shift(1).rolling(8).sum() >= 6
    return _b(was_squeezed & ~f.squeeze_on.astype(bool) & (f.close < f.bb_mid) & (f.macd_hist < 0))


# 8. Giro de Supertrend alineado con la temporalidad superior
def _supertrend_long(f: pd.DataFrame) -> pd.Series:
    flip = _b((f.supertrend_dir == 1) & (f.supertrend_dir.shift(1) == -1))
    htf_ok = _b(f.htf_ema50 > f.htf_ema200) if "htf_ema50" in f else _b(f.ema50 > f.ema200)
    return flip & htf_ok


def _supertrend_short(f: pd.DataFrame) -> pd.Series:
    flip = _b((f.supertrend_dir == -1) & (f.supertrend_dir.shift(1) == 1))
    htf_ok = _b(f.htf_ema50 < f.htf_ema200) if "htf_ema50" in f else _b(f.ema50 < f.ema200)
    return flip & htf_ok


TRIGGERS: list[Trigger] = [
    Trigger("pullback_ema", "Pullback a la EMA20 en tendencia", _pullback_long, _pullback_short),
    Trigger("donchian_breakout", "Ruptura Donchian 20 con ADX", _breakout_long, _breakout_short),
    Trigger("macd_cross", "Cruce de MACD a favor de tendencia", _macd_long, _macd_short),
    Trigger("rsi_pullback", "Pullback de RSI (40/60)", _rsi_long, _rsi_short),
    Trigger("bollinger_reversion", "Reversion en Bollinger (rango)", _bollinger_long, _bollinger_short),
    Trigger("structure_break", "Ruptura de estructura", _bos_long, _bos_short),
    Trigger("squeeze_breakout", "Salida de compresion", _squeeze_long, _squeeze_short),
    Trigger("supertrend_flip", "Giro de Supertrend", _supertrend_long, _supertrend_short),
]

TRIGGER_KEYS = [t.key for t in TRIGGERS]
TRIGGER_NAMES = {t.key: t.name for t in TRIGGERS}


def compute_triggers(features: pd.DataFrame) -> pd.DataFrame:
    """Columnas trig_<clave>: +1 si dispara en largo, -1 en corto, 0 si no
    (o si dispara en ambos sentidos a la vez, que es ruido)."""
    out = {}
    for t in TRIGGERS:
        long_ = t.long(features)
        short_ = t.short(features)
        col = pd.Series(0.0, index=features.index)
        col[long_ & ~short_] = 1.0
        col[short_ & ~long_] = -1.0
        out[f"trig_{t.key}"] = col
    return pd.DataFrame(out, index=features.index)
