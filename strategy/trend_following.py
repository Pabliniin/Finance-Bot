"""Seguimiento de tendencia: cruce de EMA rapida/lenta confirmado por fuerza
de tendencia (ADX). Parametros FIJOS para todos los instrumentos - ver
prohibicion de re-optimizar en strategy/base.py.

Logica: entra en la direccion del cruce solo si ADX confirma que hay
tendencia real (por debajo del umbral, un cruce de medias en un mercado
lateral genera casi puro ruido - es la causa mas comun de que esta familia de
estrategias pierda dinero en la practica).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.base import SIGNAL_COLUMNS, Strategy

_ADX_TREND_THRESHOLD = 25.0
_ATR_STOP_MULT = 1.5
_ATR_TARGET_MULT = 3.0  # ratio riesgo:beneficio 1:2


class TrendFollowingStrategy(Strategy):
    name = "trend_following"

    def generate_signals(self, features: pd.DataFrame) -> pd.DataFrame:
        required = ["ema_fast", "ema_slow", "adx", "atr", "close"]
        df = features.dropna(subset=required).copy()
        if df.empty:
            return self.empty_signals()

        cross_up = (df["ema_fast"] > df["ema_slow"]) & (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
        cross_down = (df["ema_fast"] < df["ema_slow"]) & (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1))
        trending = df["adx"] >= _ADX_TREND_THRESHOLD

        rows = []
        for idx in df.index[cross_up & trending]:
            row = df.loc[idx]
            entry = float(row.close)
            stop = entry - float(row.atr) * _ATR_STOP_MULT
            target = entry + float(row.atr) * _ATR_TARGET_MULT
            rows.append(self.build_signal(
                row.ts, row.instrument, row.timeframe, self.name, "long", entry, stop, target,
                confidence=_confidence_from_adx(row.adx),
                reason=(
                    f"EMA rapida cruza por encima de la lenta con ADX={row.adx:.1f} "
                    f"(>= {_ADX_TREND_THRESHOLD:.0f}, tendencia confirmada)"
                ),
            ))

        for idx in df.index[cross_down & trending]:
            row = df.loc[idx]
            entry = float(row.close)
            stop = entry + float(row.atr) * _ATR_STOP_MULT
            target = entry - float(row.atr) * _ATR_TARGET_MULT
            rows.append(self.build_signal(
                row.ts, row.instrument, row.timeframe, self.name, "short", entry, stop, target,
                confidence=_confidence_from_adx(row.adx),
                reason=(
                    f"EMA rapida cruza por debajo de la lenta con ADX={row.adx:.1f} "
                    f"(>= {_ADX_TREND_THRESHOLD:.0f}, tendencia confirmada)"
                ),
            ))

        if not rows:
            return self.empty_signals()
        result = pd.DataFrame(rows, columns=SIGNAL_COLUMNS).sort_values("ts").reset_index(drop=True)
        self.validate_signals(result)
        return result


def _confidence_from_adx(adx: float) -> float:
    # Mapeo lineal simple ADX 25->50 => confianza 0.5->1.0, acotado. No es una
    # probabilidad calibrada, solo un ranking relativo entre señales propias.
    return float(np.clip(0.5 + (adx - _ADX_TREND_THRESHOLD) / 50.0, 0.3, 1.0))
