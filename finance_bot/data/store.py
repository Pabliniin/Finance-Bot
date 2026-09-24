"""Almacen local de velas (Parquet, un fichero por simbolo y año).

Se guardan dos resoluciones base, ambas velas BID de Dukascopy:
- "h1": historico largo (2012 en adelante) para construir H1/H4/D1.
- "m1": historico reciente para M15 y alimentacion en vivo.
Todas las temporalidades se construyen desde aqui con el MISMO codigo
(finance_bot/data/bars.py) en backtest y en vivo.

Convencion de tiempo: indice = APERTURA de la vela, UTC, tz-aware.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]
M1_COLUMNS = BAR_COLUMNS  # compatibilidad


def empty_bars() -> pd.DataFrame:
    index = pd.DatetimeIndex([], tz="UTC", name="time").as_unit("ns")
    return pd.DataFrame(columns=BAR_COLUMNS, index=index, dtype="float64")


empty_m1 = empty_bars


class BarStore:
    def __init__(self, root: Path, resolution: str = "m1"):
        if resolution not in ("m1", "h1"):
            raise ValueError("resolution debe ser 'm1' o 'h1'")
        self.resolution = resolution
        self.root = Path(root) / resolution

    def _symbol_dir(self, symbol: str) -> Path:
        path = self.root / symbol
        path.mkdir(parents=True, exist_ok=True)
        return path

    def year_path(self, symbol: str, year: int) -> Path:
        return self._symbol_dir(symbol) / f"{year}.parquet"

    @staticmethod
    def _tmp_path(target: Path) -> Path:
        """Temporal UNICO por proceso: si `download` corre a la vez que el bot, dos
        procesos escribiendo el mismo `.tmp` podrian dejar un parquet a medias."""
        return target.with_name(f"{target.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")

    def years(self, symbol: str) -> list[int]:
        return sorted(int(p.stem) for p in self._symbol_dir(symbol).glob("*.parquet"))

    def load_year(self, symbol: str, year: int) -> pd.DataFrame:
        path = self.year_path(symbol, year)
        if not path.exists():
            return empty_bars()
        df = pd.read_parquet(path)
        df.index = pd.DatetimeIndex(df.index).as_unit("ns")  # resolucion unica en todo el proyecto
        return df

    def load(self, symbol: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        years = self.years(symbol)
        if start is not None:
            years = [y for y in years if y >= start.year]
        if end is not None:
            years = [y for y in years if y <= end.year]
        frames = [f for f in (self.load_year(symbol, y) for y in years) if not f.empty]
        if not frames:
            return empty_bars()
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        return df

    def write(self, symbol: str, bars: pd.DataFrame, prefer_existing: bool = False) -> int:
        """Fusiona con lo ya guardado y reescribe de forma atomica los años
        afectados. En un solape gana lo NUEVO, salvo `prefer_existing=True`, que
        rellena huecos sin pisar lo que ya habia: la consolidacion nocturna con
        Dukascopy no debe sobrescribir velas que escribio MT5 en vivo (otro
        broker, otro camino de precios, re-etiquetaria señales abiertas)."""
        if bars.empty:
            return 0
        bars = bars[BAR_COLUMNS].astype("float64").sort_index()
        for year, chunk in bars.groupby(bars.index.year):
            existing = self.load_year(symbol, int(year))
            if existing.empty:
                merged = chunk
            else:
                # keep="last" -> gana el que va DESPUES en el concat
                ordered = [chunk, existing] if prefer_existing else [existing, chunk]
                merged = pd.concat(ordered)
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            merged.index.name = "time"
            target = self.year_path(symbol, int(year))
            tmp = self._tmp_path(target)
            merged.to_parquet(tmp)
            tmp.replace(target)
        return len(bars)

    # --- manifiesto de periodos ya descargados (dias o meses, en ISO) ---

    def _manifest_path(self, symbol: str) -> Path:
        return self._symbol_dir(symbol) / "manifest.json"

    def fetched(self, symbol: str) -> set[str]:
        path = self._manifest_path(symbol)
        if not path.exists():
            return set()
        return set(json.loads(path.read_text(encoding="utf-8")))

    def mark_fetched(self, symbol: str, periods: set[str]) -> None:
        path = self._manifest_path(symbol)
        tmp = self._tmp_path(path)
        tmp.write_text(json.dumps(sorted(self.fetched(symbol) | periods)), encoding="utf-8")
        tmp.replace(path)

    def last_timestamp(self, symbol: str) -> pd.Timestamp | None:
        for year in reversed(self.years(symbol)):
            df = self.load_year(symbol, year)
            if not df.empty:
                return df.index.max()
        return None
