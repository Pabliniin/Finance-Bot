from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finance_bot.config import AppConfig, CostSpec, InstrumentConfig, SwapSpec, load_config


@pytest.fixture
def cfg() -> AppConfig:
    return load_config()


@pytest.fixture
def zero_cost_gold(cfg: AppConfig) -> InstrumentConfig:
    return cfg.instrument("XAUUSD").model_copy(
        update={
            "spread": CostSpec(kind="abs", value=0.0),
            "slippage": CostSpec(kind="abs", value=0.0),
            "swap_per_night": SwapSpec(long=0.0, short=0.0),
        }
    )


def make_m1(start: str, closes: list[float], spread_hl: float = 0.0) -> pd.DataFrame:
    """Velas M1 consecutivas con los cierres dados; high/low = cierre ± spread_hl."""
    index = pd.date_range(start, periods=len(closes), freq="1min", tz="UTC").as_unit("ns")
    close = np.asarray(closes, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + spread_hl,
            "low": np.minimum(open_, close) - spread_hl,
            "close": close,
            "volume": 1.0,
        },
        index=pd.DatetimeIndex(index, name="time"),
    )


def random_walk_bars(
    n: int, freq: str = "1h", seed: int = 1, start: str = "2024-01-01", drift: float = 0.0
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n, freq=freq, tz="UTC").as_unit("ns")
    close = 2000 + np.cumsum(rng.normal(drift, 2.0, size=n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = rng.uniform(0.2, 2.0, size=n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick,
            "close": close,
            "volume": 100.0,
        },
        index=pd.DatetimeIndex(index, name="time"),
    )
