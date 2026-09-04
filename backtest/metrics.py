"""Metricas de backtest: CAGR, Sharpe, Sortino, drawdown maximo y su duracion,
profit factor, expectativa, win rate, numero de operaciones y distribucion de
retornos. Todo calculado sobre la curva de equity DIARIA reconstruida a partir
de los cierres de operacion (ver limitacion de marcado a mercado documentada
en backtest/engine.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from backtest.engine import ClosedTrade

_DAYS_PER_YEAR = 365.25


@dataclass
class BacktestMetrics:
    n_trades: int
    win_rate: float
    expectancy_eur: float
    profit_factor: float
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    avg_win_eur: float
    avg_loss_eur: float
    return_skew: float
    return_kurtosis: float

    def to_dict(self) -> dict:
        return asdict(self)


def trades_to_dataframe(trades: list[ClosedTrade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=[
            "instrument", "strategy", "direction", "ts_open", "ts_close", "entry_price",
            "exit_price", "lots", "risk_eur", "pnl_eur", "pnl_pct_of_capital", "exit_reason", "signal_reason",
        ])
    return pd.DataFrame([asdict(t) for t in trades])


def compute_metrics(
    trades: list[ClosedTrade] | pd.DataFrame,
    equity_curve: pd.Series,
    initial_capital_eur: float,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> BacktestMetrics:
    trades_df = trades if isinstance(trades, pd.DataFrame) else trades_to_dataframe(trades)

    n_trades = len(trades_df)
    if n_trades == 0:
        equity_daily = _build_daily_equity(equity_curve, initial_capital_eur, start_date, end_date)
        return BacktestMetrics(
            n_trades=0, win_rate=0.0, expectancy_eur=0.0, profit_factor=0.0,
            cagr=_cagr(equity_daily, initial_capital_eur), sharpe=0.0, sortino=0.0,
            max_drawdown_pct=0.0, max_drawdown_duration_days=0,
            avg_win_eur=0.0, avg_loss_eur=0.0, return_skew=0.0, return_kurtosis=0.0,
        )

    wins = trades_df[trades_df["pnl_eur"] > 0]["pnl_eur"]
    losses = trades_df[trades_df["pnl_eur"] < 0]["pnl_eur"]

    win_rate = len(wins) / n_trades
    expectancy_eur = trades_df["pnl_eur"].mean()
    profit_factor = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    avg_win_eur = wins.mean() if not wins.empty else 0.0
    avg_loss_eur = losses.mean() if not losses.empty else 0.0

    equity_daily = _build_daily_equity(equity_curve, initial_capital_eur, start_date, end_date)
    daily_returns = equity_daily.pct_change().dropna()

    sharpe = _sharpe(daily_returns)
    sortino = _sortino(daily_returns)
    max_dd, max_dd_duration = _drawdown(equity_daily)
    cagr = _cagr(equity_daily, initial_capital_eur)

    return_pct = trades_df["pnl_pct_of_capital"]

    return BacktestMetrics(
        n_trades=n_trades,
        win_rate=win_rate,
        expectancy_eur=float(expectancy_eur),
        profit_factor=float(profit_factor),
        cagr=cagr,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd,
        max_drawdown_duration_days=max_dd_duration,
        avg_win_eur=float(avg_win_eur),
        avg_loss_eur=float(avg_loss_eur),
        return_skew=float(return_pct.skew()) if n_trades > 2 else 0.0,
        return_kurtosis=float(return_pct.kurt()) if n_trades > 3 else 0.0,
    )


def _to_tz(ts, tz) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_convert(tz) if ts.tzinfo else ts.tz_localize(tz)


def _build_daily_equity(
    equity_curve: pd.Series, initial_capital_eur: float, start_date: pd.Timestamp, end_date: pd.Timestamp
) -> pd.Series:
    tz = equity_curve.index.tz if len(equity_curve) and equity_curve.index.tz else "UTC"
    start = _to_tz(start_date, tz)
    end = _to_tz(end_date, tz)

    series = equity_curve.copy()
    series.loc[start] = series.get(start, initial_capital_eur)
    series = series[~series.index.duplicated(keep="last")].sort_index()

    full_index = pd.date_range(start.normalize(), end.normalize(), freq="D", tz=tz)
    combined_index = series.index.union(full_index)
    equity_daily = series.reindex(combined_index).ffill().reindex(full_index).ffill()
    equity_daily.iloc[0] = equity_daily.iloc[0] if not pd.isna(equity_daily.iloc[0]) else initial_capital_eur
    return equity_daily.ffill()


def _sharpe(daily_returns: pd.Series, risk_free: float = 0.0) -> float:
    if daily_returns.empty or daily_returns.std() == 0:
        return 0.0
    excess = daily_returns - risk_free / _DAYS_PER_YEAR
    return float(excess.mean() / daily_returns.std() * np.sqrt(_DAYS_PER_YEAR))


def _sortino(daily_returns: pd.Series, risk_free: float = 0.0) -> float:
    if daily_returns.empty:
        return 0.0
    downside = daily_returns[daily_returns < 0]
    if downside.std() == 0 or downside.empty:
        return 0.0
    excess = daily_returns - risk_free / _DAYS_PER_YEAR
    return float(excess.mean() / downside.std() * np.sqrt(_DAYS_PER_YEAR))


def _drawdown(equity_daily: pd.Series) -> tuple[float, int]:
    if equity_daily.empty:
        return 0.0, 0
    running_max = equity_daily.cummax()
    dd = equity_daily / running_max - 1
    max_dd = float(dd.min())

    underwater = dd < 0
    groups = (~underwater).cumsum()
    run_lengths = underwater.groupby(groups).sum()
    max_duration = int(run_lengths.max()) if not run_lengths.empty else 0
    return max_dd, max_duration


def _cagr(equity_daily: pd.Series, initial_capital_eur: float) -> float:
    if equity_daily.empty:
        return 0.0
    days = (equity_daily.index[-1] - equity_daily.index[0]).days
    if days <= 0 or initial_capital_eur <= 0:
        return 0.0
    final = equity_daily.iloc[-1]
    if final <= 0:
        return -1.0
    return float((final / initial_capital_eur) ** (_DAYS_PER_YEAR / days) - 1)
