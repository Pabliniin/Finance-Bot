"""El formato binario se verifico contra ficheros reales de Dukascopy (0
incoherencias OHLC en dias completos de EURUSD y XAUUSD). Estos tests fijan
esa interpretacion con cargas sinteticas, sin red."""

from __future__ import annotations

import lzma
import struct
from datetime import date

import pandas as pd
import pytest

from finance_bot.data.dukascopy import decode_candles, decode_ticks, h1_month_url, m1_day_url, ticks_to_m1


def _candles(records: list[tuple[int, int, int, int, int, float]]) -> bytes:
    raw = b"".join(struct.pack(">5if", *r) for r in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


def test_urls_use_zero_based_months() -> None:
    assert m1_day_url("XAUUSD", date(2024, 3, 12)).endswith("/XAUUSD/2024/02/12/BID_candles_min_1.bi5")
    assert h1_month_url("EURUSD", 2024, 1).endswith("/EURUSD/2024/00/BID_candles_hour_1.bi5")


def test_decode_candles_maps_open_close_low_high_order() -> None:
    # registro: t, OPEN, CLOSE, LOW, HIGH, volumen (orden real de Dukascopy)
    payload = _candles([(60, 2176925, 2176725, 2176515, 2176995, 0.5)])
    bars = decode_candles(payload, pd.Timestamp("2024-03-12", tz="UTC"), divisor=1000)
    row = bars.iloc[0]
    assert bars.index[0] == pd.Timestamp("2024-03-12 00:01", tz="UTC")
    assert (row.open, row.close, row.low, row.high) == pytest.approx((2176.925, 2176.725, 2176.515, 2176.995))
    assert row.high >= max(row.open, row.close) and row.low <= min(row.open, row.close)


def test_zero_volume_filler_bars_are_dropped() -> None:
    payload = _candles([(0, 100000, 100000, 100000, 100000, 0.0), (60, 109302, 109292, 109290, 109305, 2.0)])
    bars = decode_candles(payload, pd.Timestamp("2024-03-12", tz="UTC"), divisor=1e5)
    assert len(bars) == 1
    assert bars["close"].iloc[0] == pytest.approx(1.09292)


def test_empty_payload_gives_empty_frame() -> None:
    assert decode_candles(None, pd.Timestamp("2024-03-12", tz="UTC"), 1e5).empty


def test_ticks_decode_and_aggregate_to_m1() -> None:
    raw = b"".join(
        struct.pack(">3i2f", ms, ask, bid, 1.0, 1.0)
        for ms, ask, bid in [(1000, 4337005, 4336325), (30000, 4337500, 4336900), (61000, 4338000, 4337400)]
    )
    ticks = decode_ticks(lzma.compress(raw, format=lzma.FORMAT_ALONE), pd.Timestamp("2026-09-22 09:00", tz="UTC"), 1000)
    assert ticks["bid"].iloc[0] == pytest.approx(4336.325)
    bars = ticks_to_m1(ticks)
    assert list(bars.index) == [pd.Timestamp("2026-09-22 09:00", tz="UTC"), pd.Timestamp("2026-09-22 09:01", tz="UTC")]
    first = bars.iloc[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == pytest.approx(
        (4336.325, 4336.9, 4336.325, 4336.9, 2)
    )
