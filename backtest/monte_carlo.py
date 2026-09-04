"""Tests de robustez: Monte Carlo sobre el orden de las operaciones, y
sensibilidad de parametros +/-20%.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.metrics import BacktestMetrics


@dataclass
class MonteCarloResult:
    n_iterations: int
    final_equity_samples: np.ndarray
    max_drawdown_samples: np.ndarray
    percentiles: dict[str, dict[str, float]]


def monte_carlo_trade_order(
    trades_df: pd.DataFrame, initial_capital_eur: float, n_iterations: int = 2000, seed: int | None = None
) -> MonteCarloResult:
    """Reordena aleatoriamente la secuencia de operaciones YA cerradas
    (mismas operaciones, distinto orden temporal) y recompone la curva de
    equity bajo cada orden. Si el drawdown maximo o el capital final varian
    mucho segun el orden, el resultado del backtest real depende de en que
    orden llegaron un puñado de operaciones concretas - fragil, no una
    ventaja robusta."""
    if trades_df.empty:
        raise ValueError("no hay operaciones para simular Monte Carlo")

    rng = np.random.default_rng(seed)
    pnl = trades_df["pnl_eur"].to_numpy()
    n = len(pnl)

    finals = np.empty(n_iterations)
    max_dds = np.empty(n_iterations)

    for i in range(n_iterations):
        order = rng.permutation(n)
        equity = np.concatenate([[initial_capital_eur], initial_capital_eur + np.cumsum(pnl[order])])
        running_max = np.maximum.accumulate(equity)
        dd = equity / running_max - 1
        finals[i] = equity[-1]
        max_dds[i] = dd.min()

    return MonteCarloResult(
        n_iterations=n_iterations,
        final_equity_samples=finals,
        max_drawdown_samples=max_dds,
        percentiles={"final_equity_eur": _percentiles(finals), "max_drawdown_pct": _percentiles(max_dds)},
    )


def _percentiles(arr: np.ndarray) -> dict[str, float]:
    return {f"p{p}": float(np.percentile(arr, p)) for p in (5, 25, 50, 75, 95)}


@dataclass
class SensitivityResult:
    parameter: str
    base_value: float
    perturbation_pct: float
    base_metrics: BacktestMetrics
    low_metrics: BacktestMetrics
    high_metrics: BacktestMetrics

    @property
    def is_robust(self) -> bool:
        """El signo de la expectativa no debe cambiar entre -20%/base/+20%.
        Si cambia, el resultado depende de un ajuste fino del parametro -
        sintoma clasico de overfitting aunque el parametro nunca se haya
        "optimizado" formalmente."""
        signs = {
            self.low_metrics.expectancy_eur > 0,
            self.base_metrics.expectancy_eur > 0,
            self.high_metrics.expectancy_eur > 0,
        }
        return len(signs) == 1


def parameter_sensitivity(
    parameter: str,
    base_value: float,
    run_backtest_with_param: Callable[[float], BacktestMetrics],
    perturbation_pct: float = 0.20,
) -> SensitivityResult:
    return SensitivityResult(
        parameter=parameter,
        base_value=base_value,
        perturbation_pct=perturbation_pct,
        base_metrics=run_backtest_with_param(base_value),
        low_metrics=run_backtest_with_param(base_value * (1 - perturbation_pct)),
        high_metrics=run_backtest_with_param(base_value * (1 + perturbation_pct)),
    )
