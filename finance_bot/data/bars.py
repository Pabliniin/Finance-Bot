"""Construccion de velas M15/H1/H4/D1 a partir de M1, identica en backtest y
en vivo.

- M15 y H1: alineadas al reloj UTC (igual en cualquier broker con desfase de
  horas enteras).
- H4 y D1: alineadas a la sesion forex que abre a las 17:00 de Nueva York
  (convencion de los brokers con servidor GMT+2/GMT+3 como XM). Con D1 a
  medianoche UTC apareceria una vela "de domingo" de pocas horas que no
  existe en MT5 y el backtest no reproduciria lo que el usuario ve.

Convencion: indice = apertura de la vela (UTC); columna close_time = cierre.
Una vela solo existe si su periodo ya termino (ver `complete_until`): una vela
en formacion jamas llega a indicadores ni estrategias.
"""

from __future__ import annotations

import pandas as pd

TF_MINUTES: dict[str, int] = {"M1": 1, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
HIGHER_TF: dict[str, str | None] = {"M1": "M15", "M15": "H1", "H1": "H4", "H4": "D1", "D1": None}

_NY = "America/New_York"
_SESSION_SHIFT = pd.Timedelta(hours=7)  # 17:00 NY + 7h = 00:00 -> los bins de dia/4h caen en las fronteras de sesion

_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def infer_base_minutes(base: pd.DataFrame) -> int:
    """Resolucion de la base (1 para M1, 60 para H1) a partir del menor salto
    entre velas consecutivas."""
    if len(base) < 2:
        return 1
    diffs = base.index.to_series().diff().dropna()
    return max(1, int(diffs.min().total_seconds() // 60))


def resample(
    base: pd.DataFrame,
    tf: str,
    complete_until: pd.Timestamp | None = None,
    base_minutes: int | None = None,
) -> pd.DataFrame:
    """Agrega velas base (M1 o H1) a la temporalidad `tf`. Solo devuelve velas
    cuyo periodo haya terminado antes de `complete_until` (por defecto, el
    cierre de la ultima vela base)."""
    if tf not in TF_MINUTES:
        raise ValueError(f"temporalidad desconocida: {tf}")
    base_minutes = base_minutes or infer_base_minutes(base)
    if TF_MINUTES[tf] < base_minutes:
        raise ValueError(f"no se puede construir {tf} desde una base de {base_minutes} minutos")
    if base.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_time"])

    if tf in ("M1", "M15", "H1"):
        freq = f"{TF_MINUTES[tf]}min"
        bars = base.resample(freq, label="left", closed="left").agg(_AGG).dropna(subset=["open"])
        bars["close_time"] = bars.index + pd.Timedelta(minutes=TF_MINUTES[tf])
    else:
        bars = _resample_session_aligned(base, tf)

    if complete_until is None:
        complete_until = base.index.max() + pd.Timedelta(minutes=base_minutes)
    bars = bars[bars["close_time"] <= complete_until]
    bars.index.name = "time"
    return bars


def _resample_session_aligned(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    wall = m1.index.tz_convert(_NY).tz_localize(None) + _SESSION_SHIFT
    freq = "1D" if tf == "D1" else "4h"
    keys = wall.floor(freq)
    grouped = m1.groupby(keys).agg(_AGG)

    open_wall = grouped.index - _SESSION_SHIFT
    open_utc = open_wall.tz_localize(_NY, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")
    grouped.index = pd.DatetimeIndex(open_utc, name="time")
    grouped["close_time"] = grouped.index + pd.Timedelta(minutes=TF_MINUTES[tf])
    return grouped


def build_all(
    m1: pd.DataFrame, timeframes: list[str], complete_until: pd.Timestamp | None = None
) -> dict[str, pd.DataFrame]:
    return {tf: resample(m1, tf, complete_until) for tf in timeframes}


def validate_bars(bars: pd.DataFrame) -> list[str]:
    """Problemas de integridad (lista vacia = limpio)."""
    issues: list[str] = []
    if bars.empty:
        return issues
    prices = bars[["open", "high", "low", "close"]]
    if prices.isna().any().any():
        issues.append("precios nulos")
    if (prices <= 0).any().any():
        issues.append("precios <= 0")
    if (bars["high"] < prices[["open", "close", "low"]].max(axis=1)).any():
        issues.append("high menor que open/close/low")
    if (bars["low"] > prices[["open", "close", "high"]].min(axis=1)).any():
        issues.append("low mayor que open/close/high")
    if bars.index.has_duplicates:
        issues.append("velas duplicadas")
    if not bars.index.is_monotonic_increasing:
        issues.append("indice no ordenado")
    return issues
