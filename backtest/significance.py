"""Significancia estadistica: comparacion contra señales aleatorias de la
misma frecuencia, Deflated Sharpe Ratio (penaliza por numero de combinaciones
probadas) y correccion Benjamini-Hochberg (FDR) sobre el universo completo.

Con 30 instrumentos x 3 estrategias x 3 timeframes (270 combinaciones), elegir
"la mejor" sin estas correcciones encontraria algo con buena pinta por puro
azar casi con seguridad. Ninguna combinacion se declara valida sin pasar por
aqui.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from backtest.metrics import BacktestMetrics
from strategy.base import SIGNAL_COLUMNS

_EULER_MASCHERONI = 0.5772156649


def generate_random_baseline_signals(
    features: pd.DataFrame,
    instrument: str,
    timeframe: str,
    n_signals: int,
    atr_multiple_stop: float = 1.5,
    atr_multiple_target: float = 2.25,
    seed: int | None = None,
) -> pd.DataFrame:
    """Señales aleatorias con la MISMA frecuencia (n_signals) que la
    estrategia real, direccion aleatoria, y stop/target dimensionados con el
    ATR del propio instante (point-in-time, no con informacion futura) para
    que la comparacion sea justa: la unica diferencia con la estrategia real
    debe ser CUANDO y en que direccion se entra, no el tamaño del riesgo."""
    rng = np.random.default_rng(seed)
    eligible = features.dropna(subset=["atr"])
    n_signals = min(n_signals, len(eligible))
    if n_signals == 0:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    chosen = eligible.loc[rng.choice(eligible.index, size=n_signals, replace=False)].sort_values("ts")
    directions = rng.choice(["long", "short"], size=len(chosen))

    rows = []
    for (_, row), direction in zip(chosen.iterrows(), directions, strict=True):
        sign = 1 if direction == "long" else -1
        entry = row["close"]
        rows.append({
            "ts": row["ts"],
            "instrument": instrument,
            "timeframe": timeframe,
            "strategy": "random_baseline",
            "direction": direction,
            "entry_price": entry,
            "stop_loss": entry - sign * row["atr"] * atr_multiple_stop,
            "take_profit": entry + sign * row["atr"] * atr_multiple_target,
            "confidence": 0.5,
            "reason": "baseline aleatorio para test de significancia contra azar",
        })
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


@dataclass
class RandomBaselineTest:
    metric: str
    real_value: float
    random_mean: float
    random_std: float
    p_value: float
    n_iterations: int

    def passes(self, alpha: float) -> bool:
        return self.p_value < alpha


def random_signal_significance_test(
    real_metrics: BacktestMetrics,
    run_backtest_with_random_signals: Callable[[int], BacktestMetrics],
    n_iterations: int = 500,
    metric: str = "expectancy_eur",
) -> RandomBaselineTest:
    """p_value = proporcion de simulaciones aleatorias que igualan o superan
    el resultado real. Un p_value alto significa que un generador de señales
    sin ninguna logica habria conseguido lo mismo con facilidad."""
    real_value = getattr(real_metrics, metric)
    random_values = np.array(
        [getattr(run_backtest_with_random_signals(seed), metric) for seed in range(n_iterations)]
    )
    p_value = float((random_values >= real_value).mean())
    return RandomBaselineTest(
        metric=metric, real_value=float(real_value), random_mean=float(random_values.mean()),
        random_std=float(random_values.std()), p_value=p_value, n_iterations=n_iterations,
    )


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    n_returns: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Bailey & Lopez de Prado (2014), 'The Deflated Sharpe Ratio'. Devuelve
    P(SR verdadero > 0 | se probaron n_trials combinaciones), no solo el
    p-valor de un unico test. kurtosis es NO-exceso (una normal vale 3.0).

    Simplificacion asumida: la varianza del Sharpe estimado entre trials se
    aproxima por 1/sqrt(n_returns), razonable cuando no se dispone de la
    covarianza real entre las combinaciones probadas (que aqui no se estima
    por separado)."""
    if n_trials <= 1:
        expected_max_sharpe_null = 0.0
    else:
        sr_std = 1.0 / np.sqrt(max(n_returns, 2))
        expected_max_sharpe_null = sr_std * (
            (1 - _EULER_MASCHERONI) * stats.norm.ppf(1 - 1 / n_trials)
            + _EULER_MASCHERONI * stats.norm.ppf(1 - 1 / (n_trials * np.e))
        )

    denom = np.sqrt(max(1e-12, 1 - skew * observed_sharpe + ((kurtosis - 1) / 4) * observed_sharpe**2))
    z = (observed_sharpe - expected_max_sharpe_null) * np.sqrt(max(n_returns - 1, 1)) / denom
    return float(stats.norm.cdf(z))


def benjamini_hochberg(p_values: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Correccion FDR estandar: ordena los p-valores, encuentra el mayor rango
    k tal que p_(k) <= (k/m)*alpha, y declara supervivientes todas las
    combinaciones con rango <= k. Devuelve {clave: sobrevive_bool}."""
    items = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(items)
    if m == 0:
        return {}

    threshold_rank = 0
    for rank, (_, p) in enumerate(items, start=1):
        if p <= (rank / m) * alpha:
            threshold_rank = rank

    return {key: rank <= threshold_rank for rank, (key, _) in enumerate(items, start=1)}
