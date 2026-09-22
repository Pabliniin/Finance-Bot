from __future__ import annotations

import numpy as np
import pandas as pd

from finance_bot.data.bars import resample, validate_bars
from tests.conftest import make_m1


def _week_of_m1(start_utc: str, days: float) -> pd.DataFrame:
    n = int(days * 24 * 60)
    return make_m1(start_utc, list(2000 + np.arange(n) * 0.01))


def test_m15_aggregates_ohlcv() -> None:
    m1 = make_m1("2024-03-12 10:00", [1.0, 3.0, 2.0] + [2.5] * 12)
    bars = resample(m1, "M15", base_minutes=1)
    first = bars.iloc[0]
    assert bars.index[0] == pd.Timestamp("2024-03-12 10:00", tz="UTC")
    assert first.close_time == pd.Timestamp("2024-03-12 10:15", tz="UTC")
    assert first.open == 1.0 and first.high == 3.0 and first.low == 1.0 and first.close == 2.5
    assert first.volume == 15


def test_d1_aligned_to_new_york_close_winter_and_summer() -> None:
    winter = resample(_week_of_m1("2024-01-14 22:00", 5), "D1", base_minutes=1)
    summer = resample(_week_of_m1("2024-07-14 21:00", 5), "D1", base_minutes=1)
    assert set(winter.index.hour) == {22}  # 17:00 NY en horario estandar = 22:00 UTC
    assert set(summer.index.hour) == {21}  # 17:00 NY en horario de verano = 21:00 UTC


def test_no_sunday_stub_candle_five_d1_bars_per_week() -> None:
    bars = resample(_week_of_m1("2024-01-14 22:00", 5), "D1", base_minutes=1)
    assert len(bars) == 5
    # la sesion que abre el domingo 22:00 UTC es la del lunes: una vela completa, no un "domingo" de 2 horas
    assert all((bars["close_time"] - bars.index) == pd.Timedelta(days=1))


def test_h4_boundaries_follow_session() -> None:
    bars = resample(_week_of_m1("2024-01-14 22:00", 2), "H4", base_minutes=1)
    assert set(bars.index.hour) <= {22, 2, 6, 10, 14, 18}


def test_incomplete_bar_is_never_returned() -> None:
    m1 = make_m1("2024-03-12 10:00", [1.0] * 70)  # 10:00 -> 11:09
    bars = resample(m1, "H1", base_minutes=1)
    assert list(bars.index) == [pd.Timestamp("2024-03-12 10:00", tz="UTC")]  # la de las 11:00 sigue abierta
    bars_later = resample(m1, "H1", complete_until=pd.Timestamp("2024-03-12 12:00", tz="UTC"), base_minutes=1)
    assert len(bars_later) == 2


def test_h1_base_builds_higher_timeframes() -> None:
    idx = pd.date_range("2024-01-14 22:00", periods=48, freq="1h", tz="UTC").as_unit("ns")
    h1 = pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=pd.DatetimeIndex(idx, name="time")
    )
    d1 = resample(h1, "D1", base_minutes=60)
    assert len(d1) == 2 and d1["volume"].iloc[0] == 24


def test_validate_bars_flags_inconsistencies() -> None:
    m1 = make_m1("2024-03-12 10:00", [1.0, 1.1, 1.2])
    assert validate_bars(m1) == []
    broken = m1.copy()
    broken.iloc[1, broken.columns.get_loc("high")] = 0.5
    assert any("high" in issue for issue in validate_bars(broken))
