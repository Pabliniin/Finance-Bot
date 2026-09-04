"""Validacion de integridad de OHLCV antes de aceptar cualquier lote en cache.

Filosofia: mejor un falso positivo (marcar como "hueco sospechoso" un cierre
por festivo que no conocemos) que un falso negativo (aceptar en silencio una
vela corrupta). Todo lo que aqui se marca como issue queda en ingestion_log
via data/storage/cache.log_ingestion, visible en /estado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from data.providers.base import Timeframe

# Aproximacion del cierre semanal del mercado forex/XAU en UTC. XM puede variar
# unos minutos segun el simbolo, pero como ventana de tolerancia para no
# confundir "fin de semana" con "hueco real" es suficiente.
_WEEKEND_CLOSE_DAY = 4   # viernes (Monday=0)
_WEEKEND_CLOSE_HOUR = 21
_WEEKEND_REOPEN_DAY = 6  # domingo
_WEEKEND_REOPEN_HOUR = 21


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ValidationIssue:
    kind: str
    severity: Severity
    detail: str


@dataclass
class ValidationReport:
    rows_checked: int
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict:
        return {
            "rows_checked": self.rows_checked,
            "issues": [{"kind": i.kind, "severity": i.severity.value, "detail": i.detail} for i in self.issues],
        }


def validate_ohlcv(df: pd.DataFrame, timeframe: Timeframe) -> ValidationReport:
    issues: list[ValidationIssue] = []

    if df.empty:
        return ValidationReport(rows_checked=0, issues=[])

    issues += _check_nulls(df)
    issues += _check_positive_prices(df)
    issues += _check_ohlc_consistency(df)
    issues += _check_duplicates(df)
    issues += _check_gaps(df, timeframe)

    return ValidationReport(rows_checked=len(df), issues=issues)


def _check_nulls(df: pd.DataFrame) -> list[ValidationIssue]:
    required = ["ts", "open", "high", "low", "close"]
    null_counts = df[required].isna().sum()
    bad = null_counts[null_counts > 0]
    if bad.empty:
        return []
    return [
        ValidationIssue(
            kind="null_values",
            severity=Severity.ERROR,
            detail=f"columnas con nulos: {bad.to_dict()}",
        )
    ]


def _check_positive_prices(df: pd.DataFrame) -> list[ValidationIssue]:
    price_cols = ["open", "high", "low", "close"]
    non_positive = df[(df[price_cols] <= 0).any(axis=1)]
    if non_positive.empty:
        return []
    return [
        ValidationIssue(
            kind="non_positive_price",
            severity=Severity.ERROR,
            detail=f"{len(non_positive)} velas con precio <= 0, ej. ts={non_positive['ts'].iloc[0]}",
        )
    ]


def _check_ohlc_consistency(df: pd.DataFrame) -> list[ValidationIssue]:
    high_ok = (df["high"] >= df[["open", "close", "low"]].max(axis=1))
    low_ok = (df["low"] <= df[["open", "close", "high"]].min(axis=1))
    bad = df[~(high_ok & low_ok)]
    if bad.empty:
        return []
    return [
        ValidationIssue(
            kind="ohlc_inconsistent",
            severity=Severity.ERROR,
            detail=f"{len(bad)} velas con high/low inconsistentes con open/close, "
                   f"ej. ts={bad['ts'].iloc[0]}",
        )
    ]


def _check_duplicates(df: pd.DataFrame) -> list[ValidationIssue]:
    dupes = df[df.duplicated(subset="ts", keep=False)]
    if dupes.empty:
        return []
    return [
        ValidationIssue(
            kind="duplicate_timestamp",
            severity=Severity.ERROR,
            detail=f"{dupes['ts'].nunique()} timestamps duplicados",
        )
    ]


def _check_gaps(df: pd.DataFrame, timeframe: Timeframe) -> list[ValidationIssue]:
    ordered = df.sort_values("ts")
    expected_step = timeframe.timedelta
    deltas = ordered["ts"].diff().dropna()

    unexplained = []
    for idx, delta in deltas.items():
        if delta <= expected_step:
            continue
        gap_start = ordered["ts"].iloc[ordered.index.get_loc(idx) - 1]
        gap_end = ordered["ts"].loc[idx]
        if not _is_weekend_gap(gap_start, gap_end):
            unexplained.append((gap_start, gap_end, delta))

    if not unexplained:
        return []
    worst = max(unexplained, key=lambda g: g[2])
    return [
        ValidationIssue(
            kind="unexplained_gap",
            severity=Severity.WARNING,
            detail=(
                f"{len(unexplained)} huecos no atribuibles a cierre de fin de semana "
                f"(puede ser festivo o fallo de fuente); el mayor: {worst[0]} -> {worst[1]} "
                f"({worst[2]})"
            ),
        )
    ]


def _is_weekend_gap(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    """True si [start, end] cae dentro (o se solapa razonablemente) con el
    cierre de fin de semana del mercado. No intenta modelar festivos."""
    close = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while close.dayofweek != _WEEKEND_CLOSE_DAY:
        close += pd.Timedelta(days=1)
    close = close.replace(hour=_WEEKEND_CLOSE_HOUR)

    reopen = close
    while reopen.dayofweek != _WEEKEND_REOPEN_DAY:
        reopen += pd.Timedelta(days=1)
    reopen = reopen.replace(hour=_WEEKEND_REOPEN_HOUR)

    return start <= reopen and end >= close
