"""Almacen de velas: la consolidacion rellena huecos sin pisar lo de MT5, y el
temporal de escritura es unico por proceso (no se pisan dos escrituras)."""

from __future__ import annotations

import pandas as pd

from finance_bot.data.store import BarStore
from tests.conftest import make_m1


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def test_prefer_existing_fills_gaps_without_overwriting(tmp_path) -> None:
    store = BarStore(tmp_path, "m1")
    store.write("XAUUSD", make_m1("2026-09-24 10:00", [100.0, 101.0, 102.0]))  # velas "de MT5"
    # Dukascopy trae el mismo rango con OTROS precios y una vela nueva (10:03)
    store.write("XAUUSD", make_m1("2026-09-24 10:01", [999.0, 999.0, 999.0]), prefer_existing=True)
    out = store.load("XAUUSD")
    assert out.loc[_ts("2026-09-24 10:01"), "close"] == 101.0  # se conserva MT5, no el 999
    assert out.loc[_ts("2026-09-24 10:02"), "close"] == 102.0
    assert out.loc[_ts("2026-09-24 10:03"), "close"] == 999.0  # el hueco si se rellena


def test_default_write_lets_new_data_win(tmp_path) -> None:
    store = BarStore(tmp_path, "m1")
    store.write("XAUUSD", make_m1("2026-09-24 10:00", [100.0, 101.0]))
    store.write("XAUUSD", make_m1("2026-09-24 10:00", [999.0, 999.0]))  # por defecto gana lo nuevo
    out = store.load("XAUUSD")
    assert out.loc[_ts("2026-09-24 10:00"), "close"] == 999.0


def test_tmp_path_is_unique_per_call(tmp_path) -> None:
    target = tmp_path / "2024.parquet"
    a = BarStore._tmp_path(target)
    b = BarStore._tmp_path(target)
    assert a != b
    assert a.name.endswith(".tmp") and a.name.startswith("2024.parquet")
