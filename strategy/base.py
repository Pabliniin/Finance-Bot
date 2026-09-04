"""Contrato comun de toda estrategia. Se define aqui, antes que las estrategias
concretas (trend_following.py, mean_reversion.py, news_event.py), porque
backtest/engine.py necesita saber la forma de una señal para poder simularla
independientemente de que estrategia la produjo.

Prohibido explicito del proyecto: ninguna implementacion de Strategy puede
optimizar sus propios parametros mirando todo el historico y llamar a eso
"resultado". Los parametros de una estrategia son fijos de antemano; el
walk-forward existe precisamente para exponer si esos parametros fijos
generalizan o no.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

SIGNAL_COLUMNS = [
    "ts", "instrument", "timeframe", "strategy", "direction",
    "entry_price", "stop_loss", "take_profit", "confidence", "reason",
]


class Strategy(ABC):
    name: str

    @abstractmethod
    def generate_signals(self, features: pd.DataFrame) -> pd.DataFrame:
        """features: la matriz devuelta por features.pipeline.build_feature_matrix
        (ya point-in-time: la fila i solo contiene informacion disponible en
        features['ts'].iloc[i]). Debe devolver un DataFrame con SIGNAL_COLUMNS,
        una fila por barra en la que la estrategia dispara (vacio si ninguna).

        direction: 'long' | 'short'. confidence: 0.0-1.0, no es una
        probabilidad calibrada salvo que la propia estrategia la calibre
        explicitamente contra su propio historial - por defecto es solo un
        ranking relativo entre señales de la misma estrategia.
        """
        raise NotImplementedError

    @staticmethod
    def empty_signals() -> pd.DataFrame:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    @staticmethod
    def build_signal(
        ts, instrument: str, timeframe: str, strategy: str, direction: str,
        entry_price: float, stop_loss: float, take_profit: float, confidence: float, reason: str,
    ) -> dict:
        return {
            "ts": ts, "instrument": instrument, "timeframe": timeframe, "strategy": strategy,
            "direction": direction, "entry_price": entry_price, "stop_loss": stop_loss,
            "take_profit": take_profit, "confidence": confidence, "reason": reason,
        }

    @staticmethod
    def validate_signals(df: pd.DataFrame) -> None:
        missing = set(SIGNAL_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"faltan columnas obligatorias en las señales: {missing}")
        if not df.empty:
            bad_direction = ~df["direction"].isin(["long", "short"])
            if bad_direction.any():
                raise ValueError("direction debe ser 'long' o 'short'")
            bad_confidence = ~df["confidence"].between(0.0, 1.0)
            if bad_confidence.any():
                raise ValueError("confidence debe estar en [0, 1]")
