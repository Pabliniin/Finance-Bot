"""Compara el rendimiento ESPERADO (del backtest) contra el OBSERVADO (paper
trading en vivo) y avisa de degradacion del modelo con un test estadistico,
no con una comparacion visual de dos numeros. Con pocas operaciones en vivo
cualquier win rate "parece" distinto del backtest por puro ruido de muestra
pequeña - por eso hay un minimo de operaciones antes de emitir un veredicto.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from scipy import stats

from backtest.metrics import BacktestMetrics

MIN_TRADES_FOR_ASSESSMENT = 20


@dataclass
class DivergenceReport:
    strategy: str
    n_live_trades: int
    backtest_win_rate: float
    live_win_rate: float | None
    win_rate_p_value: float | None
    backtest_expectancy_eur: float
    live_expectancy_eur: float | None
    degraded: bool
    message: str


def assess_divergence(strategy: str, backtest_metrics: BacktestMetrics, live_trades: pd.DataFrame) -> DivergenceReport:
    n = len(live_trades)
    if n < MIN_TRADES_FOR_ASSESSMENT:
        return DivergenceReport(
            strategy=strategy, n_live_trades=n, backtest_win_rate=backtest_metrics.win_rate,
            live_win_rate=None, win_rate_p_value=None, backtest_expectancy_eur=backtest_metrics.expectancy_eur,
            live_expectancy_eur=None, degraded=False,
            message=(
                f"solo {n} operaciones en vivo registradas; se necesitan al menos "
                f"{MIN_TRADES_FOR_ASSESSMENT} para evaluar divergencia con fiabilidad estadistica"
            ),
        )

    live_wins = int((live_trades["pnl_eur"] > 0).sum())
    live_win_rate = live_wins / n
    live_expectancy = float(live_trades["pnl_eur"].mean())

    # H0: la tasa de acierto en vivo es >= la del backtest. Test unilateral
    # 'less' porque solo nos interesa detectar DEGRADACION, no que vaya mejor
    # de lo esperado (eso no dispara ninguna alerta).
    test_result = stats.binomtest(live_wins, n, backtest_metrics.win_rate, alternative="less")
    p_value = float(test_result.pvalue)

    degraded = p_value < 0.05 or (live_expectancy < 0 < backtest_metrics.expectancy_eur)

    message = (
        f"win rate en vivo {live_win_rate:.1%} (n={n}) vs {backtest_metrics.win_rate:.1%} en backtest, "
        f"p={p_value:.3f} para H0 'en vivo >= backtest'"
    )
    if degraded:
        message += " -> DEGRADACION DETECTADA"

    return DivergenceReport(
        strategy=strategy, n_live_trades=n, backtest_win_rate=backtest_metrics.win_rate,
        live_win_rate=live_win_rate, win_rate_p_value=p_value,
        backtest_expectancy_eur=backtest_metrics.expectancy_eur, live_expectancy_eur=live_expectancy,
        degraded=degraded, message=message,
    )
