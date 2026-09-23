"""Candidatos a señal: velas cerradas donde al menos un trigger dispara, con
su plan de operacion (direccion, distancia del stop = 1R) y las features
alineadas con la direccion que usara el modelo de probabilidad.

El plan es identico en backtest y en vivo:
- Entrada: a mercado en cuanto cierra la vela (en backtest: apertura de la
  siguiente vela base, lo que incluye los huecos de fin de semana).
- Stop: tras el minimo (largos) / maximo (cortos) de las ultimas N velas, con
  0.25 ATR de margen, acotado entre sl_atr_min y sl_atr_max ATR.
- Objetivos: TP1 = 1R y TP2 = 2R (config.targets_r). Cierre por tiempo a las
  max_bars velas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from finance_bot.config import TimeframePlan
from finance_bot.strategies.triggers import TRIGGER_KEYS
from finance_bot.strategies.voters import VOTER_KEYS

_REQUIRED = ["ema200", "atr", "adx", "rsi", "macd", "supertrend_dir"]
SESSIONS = ("asia", "london", "overlap", "newyork", "late")


def session_of(hour_utc: pd.Series) -> pd.Series:
    bins = pd.cut(hour_utc, bins=[-1, 6, 11, 15, 20, 23], labels=list(SESSIONS))
    return bins.astype(str)


def generate_candidates(
    features: pd.DataFrame,
    symbol: str,
    tf: str,
    plan: TimeframePlan,
    allowed_hours: list[int] | None = None,
) -> pd.DataFrame:
    trig = features[[f"trig_{k}" for k in TRIGGER_KEYS]]
    longs = (trig == 1).any(axis=1)
    shorts = (trig == -1).any(axis=1)
    direction = pd.Series(0, index=features.index, dtype="int64")
    direction[longs & ~shorts] = 1
    direction[shorts & ~longs] = -1  # si hay triggers en ambos sentidos a la vez, no hay candidato

    ready = features[_REQUIRED].notna().all(axis=1) & (features["atr"] > 0)
    mask = (direction != 0) & ready
    if allowed_hours is not None:
        hours = features["close_time"].dt.hour
        mask &= hours.isin(allowed_hours)

    cand = features.loc[mask].copy()
    if cand.empty:
        return cand
    cand["direction"] = direction[mask]
    cand["symbol"] = symbol
    cand["tf"] = tf
    cand["sl_distance"] = stop_distance(features, cand, plan)

    trig_values = cand[[f"trig_{k}" for k in TRIGGER_KEYS]].to_numpy()
    dirs = cand["direction"].to_numpy()
    cand["triggers"] = [
        [k for j, k in enumerate(TRIGGER_KEYS) if trig_values[i, j] == dirs[i]] for i in range(len(cand))
    ]
    return cand


def stop_distance(features: pd.DataFrame, cand: pd.DataFrame, plan: TimeframePlan) -> np.ndarray:
    """Distancia del stop (= 1R) tras la estructura reciente, acotada en ATR."""
    lookback_low = features["low"].rolling(plan.swing_lookback).min().loc[cand.index]
    lookback_high = features["high"].rolling(plan.swing_lookback).max().loc[cand.index]
    atr = cand["atr"]
    raw_long = cand["close"] - (lookback_low - 0.25 * atr)
    raw_short = (lookback_high + 0.25 * atr) - cand["close"]
    raw = np.where(cand["direction"] == 1, raw_long, raw_short)
    return np.clip(raw, plan.sl_atr_min * atr, plan.sl_atr_max * atr)


def random_candidates(
    features: pd.DataFrame,
    symbol: str,
    tf: str,
    plan: TimeframePlan,
    allowed_hours: list[int] | None,
    n: int,
    seed: int,
) -> pd.DataFrame:
    """Entradas al AZAR (vela y direccion aleatorias) con exactamente el mismo
    plan de stop/objetivos/tiempo que los candidatos reales: la referencia
    contra la que se mide si la seleccion aporta algo."""
    ready = features[_REQUIRED].notna().all(axis=1) & (features["atr"] > 0)
    if allowed_hours is not None:
        ready &= features["close_time"].dt.hour.isin(allowed_hours)
    eligible = features.index[ready]
    if len(eligible) == 0:
        return features.iloc[0:0]
    rng = np.random.default_rng(seed)
    picked = np.sort(rng.choice(len(eligible), size=min(n, len(eligible)), replace=False))
    cand = features.loc[eligible[picked]].copy()
    cand["direction"] = rng.choice([-1, 1], size=len(cand))
    cand["symbol"] = symbol
    cand["tf"] = tf
    cand["sl_distance"] = stop_distance(features, cand, plan)
    return cand


def model_features(cand: pd.DataFrame) -> pd.DataFrame:
    """Features alineadas con la direccion: +1 significa "a favor de la
    operacion" tanto en largos como en cortos, para que un unico modelo
    aprenda de ambos sentidos."""
    d = cand["direction"].astype(float)
    atr = cand["atr"]
    out = pd.DataFrame(index=cand.index)

    for key in VOTER_KEYS:
        out[f"agree_{key}"] = d * cand[f"vote_{key}"]
    for key in TRIGGER_KEYS:
        out[f"trig_{key}"] = (cand[f"trig_{key}"] == d).astype(float)
    out["n_triggers"] = out[[f"trig_{k}" for k in TRIGGER_KEYS]].sum(axis=1)

    out["adx"] = cand["adx"] / 50
    out["atr_rank"] = cand["atr_pct_rank"].fillna(0.5)
    out["rsi_d"] = d * (cand["rsi"] - 50) / 50
    out["dist_ema200_d"] = (d * (cand["close"] - cand["ema200"]) / atr).clip(-10, 10) / 10
    out["slope200_d"] = (d * cand["ema200_slope_atr"]).clip(-5, 5).fillna(0) / 5
    out["roc_d"] = (d * cand["roc10_atr"]).clip(-5, 5).fillna(0) / 5
    bb_half = (cand["bb_upper"] - cand["bb_mid"]).where(lambda s: s > 0)
    out["bb_pos_d"] = (d * (cand["close"] - cand["bb_mid"]) / bb_half).clip(-2, 2).fillna(0)
    out["sl_atr"] = cand["sl_distance"] / atr

    # espacio hasta el siguiente nivel en contra, en R (5 = sin nivel cercano)
    room_long = (cand["resistance"] - cand["close"]) / cand["sl_distance"]
    room_short = (cand["close"] - cand["support"]) / cand["sl_distance"]
    out["room_r"] = np.where(d > 0, room_long, room_short)
    out["room_r"] = out["room_r"].fillna(5).clip(0, 5)

    htf_rsi = cand["htf_rsi"] if "htf_rsi" in cand else pd.Series(50.0, index=cand.index)
    out["htf_rsi_d"] = d * (htf_rsi.fillna(50) - 50) / 50

    for tf in ("M15", "H1", "H4", "D1"):
        out[f"tf_{tf}"] = (cand["tf"] == tf).astype(float)
    for sym in ("XAUUSD", "EURUSD"):
        out[f"sym_{sym}"] = (cand["symbol"] == sym).astype(float)
    out["is_long"] = (d > 0).astype(float)

    intraday = cand["tf"].isin(["M1", "M15", "H1"])
    session = session_of(cand["close_time"].dt.hour)
    for s in SESSIONS:
        out[f"session_{s}"] = ((session == s) & intraday).astype(float)
    return out.astype("float64")
