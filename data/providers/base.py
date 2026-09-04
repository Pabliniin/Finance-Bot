"""Interfaz comun para todo proveedor de datos de mercado.

Contrato point-in-time (critico, no negociable en este proyecto):

- Toda columna de timestamp que devuelva un provider representa el CIERRE de la
  vela, no la apertura, y va en UTC tz-aware. Los proveedores de origen (MT5
  entrega el open time; Dukascopy se agrega desde ticks) deben normalizar a
  este contrato antes de devolver el DataFrame. Mezclar convenciones de tiempo
  entre fuentes es la forma mas comun de introducir look-ahead bias sin darse
  cuenta, asi que se fuerza aqui, en un unico punto, en vez de confiar en que
  cada estrategia lo haga bien.
- Un provider NUNCA debe devolver la vela que todavia esta en formacion (su
  cierre es posterior al instante de la consulta). Si el ultimo timestamp
  disponible es igual o posterior a "ahora", se descarta esa fila.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import pandas as pd

OHLCV_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


class Timeframe(StrEnum):
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def timedelta(self) -> pd.Timedelta:
        return {
            Timeframe.H1: pd.Timedelta(hours=1),
            Timeframe.H4: pd.Timedelta(hours=4),
            Timeframe.D1: pd.Timedelta(days=1),
        }[self]


@dataclass(frozen=True)
class OHLCVRequest:
    instrument: str
    timeframe: Timeframe
    start: datetime
    end: datetime


class DataProviderError(RuntimeError):
    """Fallo al obtener datos. El caller decide si degradar o abortar, nunca inventar datos."""


class DataProvider(ABC):
    """Cada fuente de OHLCV implementa esto. Ver contrato point-in-time arriba."""

    name: str

    @abstractmethod
    def fetch_ohlcv(self, request: OHLCVRequest) -> pd.DataFrame:
        """Devuelve un DataFrame con OHLCV_COLUMNS, ordenado por ts ascendente,
        sin la vela en formacion. Lanza DataProviderError si la fuente falla
        (nunca debe devolver un DataFrame relleno de forma sintetica)."""
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Chequeo barato de salud, usado por /estado. No debe lanzar excepciones."""
        raise NotImplementedError

    @staticmethod
    def validate_columns(df: pd.DataFrame) -> None:
        missing = set(OHLCV_COLUMNS) - set(df.columns)
        if missing:
            raise DataProviderError(f"faltan columnas obligatorias en el DataFrame OHLCV: {missing}")
