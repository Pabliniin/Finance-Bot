"""Acceso unificado a velas por temporalidad, igual en investigacion y en vivo.

- M15 se construye desde M1.
- H1/H4/D1 se construyen desde H1: el historico mensual de Dukascopy y, para
  el tramo que aun no cubre (el mes en curso y el dia de hoy), desde M1
  agregado a H1. Como ambas son velas BID de la misma fuente, la costura es
  exacta en backtest; en vivo las ultimas horas pueden venir del broker (MT5).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from finance_bot.config import AppConfig
from finance_bot.data.bars import TF_MINUTES, resample
from finance_bot.data.store import BarStore, empty_bars


class MarketData:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.h1_store = BarStore(cfg.data.store_path, "h1")
        self.m1_store = BarStore(cfg.data.store_path, "m1")

    def base_m1(self, symbol: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        return self.m1_store.load(symbol, start, end)

    def base_h1(self, symbol: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        h1 = self.h1_store.load(symbol, start, end)
        covered_until = h1.index.max() + pd.Timedelta(hours=1) if not h1.empty else None
        m1_start = covered_until.to_pydatetime() if covered_until is not None else start
        m1 = self.m1_store.load(symbol, m1_start, end)
        if m1.empty:
            return h1
        from_m1 = resample(m1, "H1", base_minutes=1)[["open", "high", "low", "close", "volume"]]
        if covered_until is not None:
            from_m1 = from_m1[from_m1.index >= covered_until]
        if h1.empty:
            return from_m1
        return pd.concat([h1, from_m1]).sort_index()

    def base_for(
        self, symbol: str, tf: str, start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame:
        return self.base_m1(symbol, start, end) if tf == "M15" else self.base_h1(symbol, start, end)

    def bars(
        self,
        symbol: str,
        tf: str,
        start: datetime | None = None,
        end: datetime | None = None,
        complete_until: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        base = self.base_for(symbol, tf, start, end)
        if base.empty:
            return empty_bars()
        base_minutes = 1 if tf == "M15" else 60
        return resample(base, tf, complete_until=complete_until, base_minutes=base_minutes)

    def all_timeframes(
        self,
        symbol: str,
        timeframes: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
        complete_until: pd.Timestamp | None = None,
        m1_start: datetime | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Carga cada base una sola vez y construye todas las temporalidades.
        `m1_start` permite cargar menos historico M1 (en vivo, M15 solo
        necesita unas semanas; cargar años de M1 en cada escaneo es inutil)."""
        out: dict[str, pd.DataFrame] = {}
        m1_tfs = {"M1", "M15"}
        need_h1 = any(tf not in m1_tfs for tf in timeframes)
        need_m1 = any(tf in m1_tfs for tf in timeframes)
        h1_base = self.base_h1(symbol, start, end) if need_h1 else empty_bars()
        m1_base = self.base_m1(symbol, m1_start or start, end) if need_m1 else empty_bars()
        for tf in sorted(timeframes, key=lambda t: TF_MINUTES[t]):
            base, minutes = (m1_base, 1) if tf in m1_tfs else (h1_base, 60)
            out[tf] = (
                resample(base, tf, complete_until=complete_until, base_minutes=minutes)
                if not base.empty
                else empty_bars()
            )
        return out
