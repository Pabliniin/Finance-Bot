"""Las estrategias "votantes" de la confluencia.

Cada votante es una estrategia clasica reducida a una opinion por vela:
+1 (alcista), -1 (bajista) o 0 (sin opinion). Se agrupan en familias porque
votantes de la misma familia miden casi lo mismo (siete indicadores de
tendencia de acuerdo NO son siete confirmaciones independientes). Por eso la
probabilidad que ve el usuario no sale de "contar votos": sale del modelo
calibrado fuera de muestra (finance_bot/engine/model.py), que aprende cuanto
aporta REALMENTE cada votante, incluidas las correlaciones entre ellos.

Todos operan sobre velas cerradas con indicadores point-in-time
(finance_bot/indicators.py) y columnas de contexto de temporalidad superior
(prefijo htf_), diario (d1_) e intermercado (im_) ya alineadas en el tiempo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

FAMILIES: dict[str, str] = {
    "trend": "Tendencia",
    "momentum": "Momentum",
    "levels": "Niveles y reversion",
    "volatility": "Volatilidad",
    "context": "Temporalidades superiores",
    "intermarket": "Intermercado (USD)",
    "candles": "Velas",
}
FAMILY_SHORT: dict[str, str] = {
    "trend": "Tend.",
    "momentum": "Mom.",
    "levels": "Niveles",
    "volatility": "Vol.",
    "context": "TF sup.",
    "intermarket": "USD",
    "candles": "Velas",
}


@dataclass(frozen=True)
class Voter:
    key: str
    name: str
    family: str
    votes: Callable[[pd.DataFrame], pd.Series]
    describe: Callable[[pd.Series], str]


def _sign(cond_up: pd.Series, cond_down: pd.Series) -> pd.Series:
    out = pd.Series(0.0, index=cond_up.index)
    out[cond_up.fillna(False).astype(bool)] = 1.0
    out[cond_down.fillna(False).astype(bool)] = -1.0
    return out


def _fmt(value: float, digits: int = 1) -> str:
    return "n/d" if value is None or (isinstance(value, float) and np.isnan(value)) else f"{value:.{digits}f}"


# --- Tendencia -------------------------------------------------------------


def _ema_stack(f: pd.DataFrame) -> pd.Series:
    return _sign((f.ema20 > f.ema50) & (f.ema50 > f.ema200), (f.ema20 < f.ema50) & (f.ema50 < f.ema200))


def _ema200_trend(f: pd.DataFrame) -> pd.Series:
    return _sign((f.close > f.ema200) & (f.ema200_slope_atr > 0), (f.close < f.ema200) & (f.ema200_slope_atr < 0))


def _supertrend(f: pd.DataFrame) -> pd.Series:
    return f.supertrend_dir.fillna(0.0)


def _adx_dmi(f: pd.DataFrame) -> pd.Series:
    strong = f.adx >= 20
    return _sign(strong & (f.di_plus > f.di_minus), strong & (f.di_plus < f.di_minus))


def _ichimoku(f: pd.DataFrame) -> pd.Series:
    top = np.maximum(f.senkou_a, f.senkou_b)
    bottom = np.minimum(f.senkou_a, f.senkou_b)
    return _sign((f.close > top) & (f.tenkan > f.kijun), (f.close < bottom) & (f.tenkan < f.kijun))


def _structure(f: pd.DataFrame) -> pd.Series:
    return f.structure.fillna(0.0)


def _donchian_position(f: pd.DataFrame) -> pd.Series:
    width = f.donchian_high55 - f.donchian_low55
    pos = (f.close - f.donchian_low55) / width.where(width > 0)
    return _sign(pos > 0.75, pos < 0.25)


# --- Momentum --------------------------------------------------------------


def _macd(f: pd.DataFrame) -> pd.Series:
    return _sign((f.macd_hist > 0) & (f.macd > 0), (f.macd_hist < 0) & (f.macd < 0))


def _rsi_regime(f: pd.DataFrame) -> pd.Series:
    return _sign(f.rsi > 55, f.rsi < 45)


def _stochrsi_timing(f: pd.DataFrame) -> pd.Series:
    cross_up = (f.stochrsi_k > f.stochrsi_d) & (f.stochrsi_k.shift(1) <= f.stochrsi_d.shift(1)) & (f.stochrsi_k < 50)
    cross_down = (f.stochrsi_k < f.stochrsi_d) & (f.stochrsi_k.shift(1) >= f.stochrsi_d.shift(1)) & (f.stochrsi_k > 50)
    recent_up = cross_up.rolling(3, min_periods=1).max().astype(bool)
    recent_down = cross_down.rolling(3, min_periods=1).max().astype(bool)
    return _sign(recent_up & ~recent_down, recent_down & ~recent_up)


def _roc(f: pd.DataFrame) -> pd.Series:
    return _sign(f.roc10_atr > 1.0, f.roc10_atr < -1.0)


# --- Niveles y reversion ------------------------------------------------------


def _bollinger_reversion(f: pd.DataFrame) -> pd.Series:
    return _sign((f.close <= f.bb_lower) & (f.rsi < 35), (f.close >= f.bb_upper) & (f.rsi > 65))


def _sr_room(f: pd.DataFrame) -> pd.Series:
    near_support = (f.close - f.support) <= f.atr
    near_resistance = (f.resistance - f.close) <= f.atr
    room_up = ((f.resistance - f.close) / f.atr).fillna(np.inf) >= 2.0
    room_down = ((f.close - f.support) / f.atr).fillna(np.inf) >= 2.0
    return _sign(near_support & room_up, near_resistance & room_down)


def _daily_pivot(f: pd.DataFrame) -> pd.Series:
    if "d1_pivot" not in f:
        return pd.Series(0.0, index=f.index)
    return _sign(f.close > f.d1_pivot, f.close < f.d1_pivot)


def _fib_pullback(f: pd.DataFrame) -> pd.Series:
    long_zone = (f.structure == 1) & f.retrace_long.between(0.382, 0.618)
    short_zone = (f.structure == -1) & f.retrace_short.between(0.382, 0.618)
    return _sign(long_zone, short_zone)


# --- Volatilidad ---------------------------------------------------------------


def _squeeze_release(f: pd.DataFrame) -> pd.Series:
    released = f.squeeze_on.shift(1).fillna(False).astype(bool) & ~f.squeeze_on.astype(bool)
    recent = released.rolling(3, min_periods=1).max().astype(bool)
    return _sign(recent & (f.macd_hist > 0), recent & (f.macd_hist < 0))


# --- Contexto de temporalidades superiores --------------------------------------


def _htf_trend(f: pd.DataFrame) -> pd.Series:
    if "htf_ema50" not in f:
        return pd.Series(0.0, index=f.index)
    return _sign(
        (f.htf_ema50 > f.htf_ema200) & (f.htf_supertrend_dir == 1),
        (f.htf_ema50 < f.htf_ema200) & (f.htf_supertrend_dir == -1),
    )


def _d1_trend(f: pd.DataFrame) -> pd.Series:
    if "d1_ema50" not in f:
        return pd.Series(0.0, index=f.index)
    return _sign(
        (f.d1_close > f.d1_ema200) & (f.d1_ema50 > f.d1_ema200),
        (f.d1_close < f.d1_ema200) & (f.d1_ema50 < f.d1_ema200),
    )


# --- Intermercado -------------------------------------------------------------------


def _usd_proxy(f: pd.DataFrame) -> pd.Series:
    """XAUUSD y EURUSD cotizan contra el USD: si el OTRO instrumento sube con
    tendencia, el dolar se esta debilitando, lo que favorece subidas en este."""
    if "im_ema50" not in f:
        return pd.Series(0.0, index=f.index)
    return _sign(
        (f.im_ema50 > f.im_ema200) & (f.im_close > f.im_ema50),
        (f.im_ema50 < f.im_ema200) & (f.im_close < f.im_ema50),
    )


def _candles(f: pd.DataFrame) -> pd.Series:
    return f.candle_pattern.fillna(0.0)


VOTERS: list[Voter] = [
    Voter(
        "ema_stack",
        "Alineacion EMA 20/50/200",
        "trend",
        _ema_stack,
        lambda r: f"EMA20 {_fmt(r.ema20, 2)} / EMA50 {_fmt(r.ema50, 2)} / EMA200 {_fmt(r.ema200, 2)}",
    ),
    Voter(
        "ema200_trend",
        "Precio y pendiente de la EMA200",
        "trend",
        _ema200_trend,
        lambda r: f"precio vs EMA200, pendiente {_fmt(r.ema200_slope_atr, 2)} ATR",
    ),
    Voter("supertrend", "Supertrend (10, 3)", "trend", _supertrend, lambda r: f"linea en {_fmt(r.supertrend, 2)}"),
    Voter(
        "adx_dmi",
        "ADX/DMI",
        "trend",
        _adx_dmi,
        lambda r: f"ADX {_fmt(r.adx)} (+DI {_fmt(r.di_plus)} / -DI {_fmt(r.di_minus)})",
    ),
    Voter(
        "ichimoku",
        "Nube de Ichimoku",
        "trend",
        _ichimoku,
        lambda r: f"tenkan {_fmt(r.tenkan, 2)} / kijun {_fmt(r.kijun, 2)}",
    ),
    Voter(
        "structure",
        "Estructura de maximos y minimos",
        "trend",
        _structure,
        lambda r: f"ultimo maximo {_fmt(r.swing_high, 2)}, ultimo minimo {_fmt(r.swing_low, 2)}",
    ),
    Voter(
        "donchian",
        "Posicion en el canal Donchian 55",
        "trend",
        _donchian_position,
        lambda r: f"canal {_fmt(r.donchian_low55, 2)} - {_fmt(r.donchian_high55, 2)}",
    ),
    Voter(
        "macd",
        "MACD (12, 26, 9)",
        "momentum",
        _macd,
        lambda r: f"MACD {_fmt(r.macd, 4)}, histograma {_fmt(r.macd_hist, 4)}",
    ),
    Voter("rsi_regime", "Regimen de RSI (14)", "momentum", _rsi_regime, lambda r: f"RSI {_fmt(r.rsi)}"),
    Voter(
        "stochrsi",
        "Cruce de Stoch RSI",
        "momentum",
        _stochrsi_timing,
        lambda r: f"K {_fmt(r.stochrsi_k)} / D {_fmt(r.stochrsi_d)}",
    ),
    Voter("roc", "Impulso de 10 velas", "momentum", _roc, lambda r: f"{_fmt(r.roc10_atr, 2)} ATR en 10 velas"),
    Voter(
        "bollinger",
        "Reversion en Bandas de Bollinger",
        "levels",
        _bollinger_reversion,
        lambda r: f"bandas {_fmt(r.bb_lower, 2)} - {_fmt(r.bb_upper, 2)}, RSI {_fmt(r.rsi)}",
    ),
    Voter(
        "sr_room",
        "Soportes y resistencias",
        "levels",
        _sr_room,
        lambda r: f"soporte {_fmt(r.support, 2)}, resistencia {_fmt(r.resistance, 2)}",
    ),
    Voter(
        "daily_pivot",
        "Pivote diario clasico",
        "levels",
        _daily_pivot,
        lambda r: f"pivote {_fmt(r.get('d1_pivot', np.nan), 2)}",
    ),
    Voter(
        "fibonacci",
        "Retroceso de Fibonacci 38-62%",
        "levels",
        _fib_pullback,
        lambda r: f"retroceso {_fmt(100 * r.retrace_long, 0) if r.structure >= 0 else _fmt(100 * r.retrace_short, 0)}%",
    ),
    Voter(
        "squeeze",
        "Salida de compresion (squeeze)",
        "volatility",
        _squeeze_release,
        lambda r: "compresion Bollinger/Keltner liberada" if not r.squeeze_on else "en compresion",
    ),
    Voter(
        "htf_trend",
        "Tendencia de la temporalidad superior",
        "context",
        _htf_trend,
        lambda r: f"EMA50 {_fmt(r.get('htf_ema50', np.nan), 2)} vs EMA200 {_fmt(r.get('htf_ema200', np.nan), 2)}",
    ),
    Voter(
        "d1_trend",
        "Tendencia diaria",
        "context",
        _d1_trend,
        lambda r: f"cierre D1 {_fmt(r.get('d1_close', np.nan), 2)} vs EMA200 {_fmt(r.get('d1_ema200', np.nan), 2)}",
    ),
    Voter(
        "usd_proxy",
        "Fuerza del dolar (otro instrumento)",
        "intermarket",
        _usd_proxy,
        lambda r: f"otro instrumento en {_fmt(r.get('im_close', np.nan), 4)}",
    ),
    Voter(
        "candles",
        "Patron de velas",
        "candles",
        _candles,
        lambda r: "envolvente/martillo" if r.candle_pattern != 0 else "sin patron",
    ),
]

VOTER_KEYS = [v.key for v in VOTERS]


def compute_votes(features: pd.DataFrame) -> pd.DataFrame:
    """Una columna vote_<clave> por votante, valores en {-1, 0, +1}."""
    return pd.DataFrame({f"vote_{v.key}": v.votes(features) for v in VOTERS}, index=features.index)
