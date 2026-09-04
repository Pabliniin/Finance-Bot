"""Division train/validacion/test y walk-forward por ventanas rodantes.

Nota importante sobre que significa "walk-forward" en este proyecto: las
estrategias tienen parametros FIJOS (prohibido re-optimizar por ventana, ver
strategy/base.py), asi que esto no es walk-forward-optimization clasico. Su
proposito aqui es distinto y mas honesto: comprobar si el rendimiento de una
estrategia de parametros fijos es ESTABLE a lo largo de distintos periodos de
mercado, o si depende de un tramo concreto del historico (senal de que lo que
parecia ventaja era en realidad ajuste, aunque no se haya optimizado a mano -
p.ej. porque el propio diseño de la regla se inspiro mirando ese tramo).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.costs import CostModel
from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics, compute_metrics, trades_to_dataframe


@dataclass
class SplitResult:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def train_validation_test_split(
    bars: pd.DataFrame, train_pct: float, validation_pct: float, test_pct: float
) -> SplitResult:
    total = train_pct + validation_pct + test_pct
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"train/validation/test deben sumar 1.0, suman {total}")

    n = len(bars)
    ordered = bars.sort_values("ts").reset_index(drop=True)
    train_end = int(n * train_pct)
    val_end = train_end + int(n * validation_pct)

    return SplitResult(
        train=ordered.iloc[:train_end],
        validation=ordered.iloc[train_end:val_end],
        test=ordered.iloc[val_end:],
    )


@dataclass
class WindowResult:
    window_index: int
    start: pd.Timestamp
    end: pd.Timestamp
    metrics: BacktestMetrics
    trades: pd.DataFrame
    ending_capital_eur: float


def run_walk_forward(
    instrument: str,
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    cost_model: CostModel,
    initial_capital_eur: float,
    risk_pct: float,
    quote_to_eur_rate_for_ts,
    n_windows: int,
) -> list[WindowResult]:
    """Ventanas contiguas, cronologicas, sin solape. El capital se compone de
    una ventana a la siguiente de forma SECUENCIAL (el resultado de la
    ventana 1 es el capital de partida de la ventana 2) - igual que ocurriria
    operando de verdad, nunca "cada ventana empieza igual" como si fueran
    independientes."""
    if n_windows < 1:
        raise ValueError("n_windows debe ser >= 1")

    bounds = _window_bounds(bars["ts"], n_windows)
    results: list[WindowResult] = []
    capital = initial_capital_eur

    for i, (start, end) in enumerate(bounds):
        window_bars = bars[(bars["ts"] >= start) & (bars["ts"] < end)]
        window_signals = (
            signals[(signals["ts"] >= start) & (signals["ts"] < end)] if not signals.empty else signals
        )
        if window_bars.empty:
            continue

        engine = BacktestEngine(cost_model, capital, risk_pct)
        trades, equity_curve = engine.run(instrument, window_bars, window_signals, quote_to_eur_rate_for_ts)
        metrics = compute_metrics(trades, equity_curve, capital, start, end)

        results.append(
            WindowResult(
                window_index=i, start=start, end=end, metrics=metrics,
                trades=trades_to_dataframe(trades), ending_capital_eur=engine.capital,
            )
        )
        capital = engine.capital

    return results


def _window_bounds(ts: pd.Series, n_windows: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start, end = ts.min(), ts.max()
    edges = pd.date_range(start, end, periods=n_windows + 1)
    return list(zip(edges[:-1], edges[1:], strict=True))
