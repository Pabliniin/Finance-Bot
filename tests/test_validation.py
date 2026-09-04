from __future__ import annotations

import pandas as pd
import pytest

from data.providers.base import Timeframe
from data.validation import validate_ohlcv


def _bars(timestamps: list[str], **overrides) -> pd.DataFrame:
    n = len(timestamps)
    df = pd.DataFrame({
        "ts": pd.to_datetime(timestamps, utc=True),
        "open": [1.1000] * n,
        "high": [1.1010] * n,
        "low": [1.0990] * n,
        "close": [1.1005] * n,
        "volume": [100.0] * n,
    })
    for col, values in overrides.items():
        df[col] = values
    return df


def test_clean_data_has_no_issues():
    df = _bars(["2024-01-02T10:00:00Z", "2024-01-02T11:00:00Z", "2024-01-02T12:00:00Z"])
    report = validate_ohlcv(df, Timeframe.H1)
    assert report.is_clean
    assert not report.has_errors


def test_empty_dataframe_is_clean():
    df = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    report = validate_ohlcv(df, Timeframe.H1)
    assert report.rows_checked == 0
    assert report.is_clean


def test_null_close_is_flagged_as_error():
    df = _bars(["2024-01-02T10:00:00Z", "2024-01-02T11:00:00Z"], close=[1.1005, None])
    report = validate_ohlcv(df, Timeframe.H1)
    assert report.has_errors
    assert any(i.kind == "null_values" for i in report.issues)


def test_non_positive_price_is_flagged_as_error():
    df = _bars(["2024-01-02T10:00:00Z", "2024-01-02T11:00:00Z"], low=[1.0990, -1.0])
    report = validate_ohlcv(df, Timeframe.H1)
    assert report.has_errors
    assert any(i.kind == "non_positive_price" for i in report.issues)


def test_high_below_close_is_inconsistent():
    # high (1.1010) queda por debajo de close (1.15): imposible fisicamente.
    df = _bars(["2024-01-02T10:00:00Z"], close=[1.15])
    report = validate_ohlcv(df, Timeframe.H1)
    assert report.has_errors
    assert any(i.kind == "ohlc_inconsistent" for i in report.issues)


def test_duplicate_timestamp_is_flagged_as_error():
    df = _bars(["2024-01-02T10:00:00Z", "2024-01-02T10:00:00Z"])
    report = validate_ohlcv(df, Timeframe.H1)
    assert report.has_errors
    assert any(i.kind == "duplicate_timestamp" for i in report.issues)


def test_weekday_gap_is_flagged_as_warning():
    # Martes 10:00 -> Martes 15:00 en H1: faltan 4 velas intermedias, y no cae
    # en fin de semana, asi que debe marcarse.
    df = _bars(["2024-01-02T10:00:00Z", "2024-01-02T15:00:00Z"])
    report = validate_ohlcv(df, Timeframe.H1)
    assert not report.has_errors
    assert any(i.kind == "unexplained_gap" and i.severity.value == "warning" for i in report.issues)


def test_weekend_gap_is_not_flagged():
    # Viernes 20:00 -> Lunes 01:00: el hueco esta contenido en el cierre de
    # fin de semana del mercado, no es un fallo de datos.
    df = _bars(["2024-01-05T20:00:00Z", "2024-01-08T01:00:00Z"])
    report = validate_ohlcv(df, Timeframe.H1)
    assert report.is_clean


@pytest.mark.parametrize("timeframe", [Timeframe.H1, Timeframe.H4, Timeframe.D1])
def test_validate_runs_for_every_configured_timeframe(timeframe):
    df = _bars(["2024-01-02T10:00:00Z", "2024-01-02T11:00:00Z"])
    report = validate_ohlcv(df, timeframe)
    assert report.rows_checked == 2
