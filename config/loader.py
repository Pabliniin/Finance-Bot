"""Carga de la configuracion NO sensible (config.yaml, instruments.yaml).
Separado de settings.py (variables de entorno / secretos) a proposito.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

_CONFIG_DIR = Path(__file__).parent


class AccountConfig(BaseModel):
    capital_reference_eur: float
    max_risk_per_trade_pct: float
    max_simultaneous_positions: int
    max_correlation_between_positions: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    drawdown_suspend_pct: float


class ScheduleConfig(BaseModel):
    daily_job_utc: str
    intraday_job_cron: str


class DataConfig(BaseModel):
    history_backfill_years: int
    provider_live: str
    provider_backtest: str
    gap_tolerance_bars: int


class NewsConfig(BaseModel):
    rss_sources: list[dict]
    calendar_source: str
    calendar_lookahead_days: int
    calendar_lookback_days: int


class BacktestConfig(BaseModel):
    train_pct: float
    validation_pct: float
    test_pct: float
    walk_forward_windows: int
    monte_carlo_iterations: int
    significance_alpha: float
    fdr_method: str
    reference_max_drawdown_pct: float | None = None


class ReportingConfig(BaseModel):
    discord_footer: str


class AppConfig(BaseModel):
    account: AccountConfig
    timeframes: dict
    schedule: ScheduleConfig
    data: DataConfig
    news: NewsConfig
    backtest: BacktestConfig
    reporting: ReportingConfig


class Instrument(BaseModel):
    symbol: str
    category: str
    pip_decimal: int


@lru_cache
def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else _CONFIG_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)


@lru_cache
def load_instruments(path: str | Path | None = None) -> list[Instrument]:
    instruments_path = Path(path) if path else _CONFIG_DIR / "instruments.yaml"
    with open(instruments_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return [Instrument.model_validate(item) for item in raw["instruments"]]
