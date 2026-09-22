"""Ensamblado de la matriz de features por temporalidad: indicadores propios +
contexto de la temporalidad superior (htf_) + contexto diario (d1_) +
intermercado (im_) + votos de las estrategias + disparos de los triggers.

Alineacion temporal: toda union entre temporalidades o instrumentos se hace
por `close_time` con merge_asof hacia atras, es decir, a la vela t solo se le
une informacion de velas que YA habian cerrado en el instante en que cierra t.
"""

from __future__ import annotations

import pandas as pd

from finance_bot.data.bars import HIGHER_TF
from finance_bot.indicators import compute_indicators
from finance_bot.strategies.triggers import compute_triggers
from finance_bot.strategies.voters import compute_votes

_HTF_COLUMNS = ["close", "ema50", "ema200", "supertrend_dir", "rsi", "adx"]
_D1_COLUMNS = ["close", "high", "low", "ema50", "ema200"]
_IM_COLUMNS = ["close", "ema50", "ema200"]


def indicator_frames(bars_by_tf: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {tf: compute_indicators(bars) for tf, bars in bars_by_tf.items() if not bars.empty}


def _attach(base: pd.DataFrame, other: pd.DataFrame, columns: list[str], prefix: str) -> pd.DataFrame:
    right = other[columns + ["close_time"]].rename(columns={c: f"{prefix}{c}" for c in columns})
    right = right.rename(columns={"close_time": f"{prefix}close_time"}).sort_values(f"{prefix}close_time")
    left = base.reset_index().sort_values("close_time")
    merged = pd.merge_asof(left, right, left_on="close_time", right_on=f"{prefix}close_time", direction="backward")
    return merged.set_index("time")


def assemble(
    tf: str,
    ind_by_tf: dict[str, pd.DataFrame],
    other_ind_by_tf: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Features completas de la temporalidad `tf` para un instrumento."""
    features = ind_by_tf[tf].copy()

    htf = HIGHER_TF[tf]
    if htf and htf in ind_by_tf:
        features = _attach(features, ind_by_tf[htf], _HTF_COLUMNS, "htf_")

    if tf != "D1" and "D1" in ind_by_tf:
        d1 = ind_by_tf["D1"].copy()
        d1["pivot"] = (d1["high"] + d1["low"] + d1["close"]) / 3  # pivote clasico de la ultima sesion cerrada
        features = _attach(features, d1, _D1_COLUMNS + ["pivot"], "d1_")

    if other_ind_by_tf and tf in other_ind_by_tf:
        features = _attach(features, other_ind_by_tf[tf], _IM_COLUMNS, "im_")

    features = features.join(compute_votes(features)).join(compute_triggers(features))
    return features


def build_features(
    bars_by_tf: dict[str, pd.DataFrame],
    other_bars_by_tf: dict[str, pd.DataFrame] | None = None,
) -> dict[str, pd.DataFrame]:
    """Matriz de features para cada temporalidad disponible."""
    ind = indicator_frames(bars_by_tf)
    other = indicator_frames(other_bars_by_tf) if other_bars_by_tf else None
    return {tf: assemble(tf, ind, other) for tf in ind}
