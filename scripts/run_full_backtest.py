"""Driver de Fase 3 (Validacion). Ejecuta, para cada combinacion instrumento x
estrategia del universo: walk-forward fuera de muestra, comparacion contra
señales aleatorias de la misma frecuencia, Deflated Sharpe Ratio, correccion
Benjamini-Hochberg (FDR) sobre TODAS las combinaciones a la vez, y Monte Carlo
de robustez sobre el orden de las operaciones. Guarda el resultado completo -
incluidas las combinaciones descartadas, sin ocultarlas - en
reports/backtest_summary.json, que es lo que lee `/backtest` en Discord.

Requiere dependencias instaladas y datos historicos ya cargados en Postgres
(Dukascopy backfill). No se ejecuta como parte de ningun despliegue
automatico: es una accion manual y deliberada.

Uso:
    python -m scripts.run_full_backtest
    python -m scripts.run_full_backtest --instruments EURUSD,GBPUSD
    python -m scripts.run_full_backtest --strategies trend_following --years 3

Aviso de honestidad: este script NO oculta resultados negativos. Si una
estrategia no bate a señales aleatorias tras la correccion FDR, se guarda
marcada como 'discarded' con el motivo, y asi es como debe verse en Discord.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

import pandas as pd

from backtest.costs import CostModel
from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics, compute_metrics
from backtest.monte_carlo import monte_carlo_trade_order
from backtest.report_io import save_backtest_report
from backtest.significance import (
    benjamini_hochberg,
    deflated_sharpe_ratio,
    generate_random_baseline_signals,
    random_signal_significance_test,
)
from backtest.walk_forward import run_walk_forward
from config.loader import load_config, load_instruments
from config.settings import get_settings
from data.providers.base import Timeframe
from data.storage.db import get_engine
from features.pipeline import build_feature_matrix
from risk.currency_conversion import CurrencyConversionError, resolve_quote_to_eur_rate
from strategy.registry import all_strategies

logger = logging.getLogger("scripts.run_full_backtest")

N_RANDOM_ITERATIONS = 200
N_MONTE_CARLO_ITERATIONS = 2000
MIN_TRADES_TO_EVALUATE = 5

_EMPTY_METRICS = BacktestMetrics(
    n_trades=0, win_rate=0.0, expectancy_eur=0.0, profit_factor=0.0, cagr=0.0, sharpe=0.0, sortino=0.0,
    max_drawdown_pct=0.0, max_drawdown_duration_days=0, avg_win_eur=0.0, avg_loss_eur=0.0,
    return_skew=0.0, return_kurtosis=0.0,
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Ejecuta el backtest completo (Fase 3 - Validacion)")
    parser.add_argument("--instruments", type=str, default=None, help="Lista separada por comas, ej. EURUSD,GBPUSD")
    parser.add_argument("--strategies", type=str, default=None, help="Lista separada por comas")
    parser.add_argument("--years", type=int, default=None, help="Años de historico (por defecto: config.yaml)")
    args = parser.parse_args()

    settings = get_settings()
    cfg = load_config()
    engine = get_engine(settings.database_url)

    instruments = load_instruments()
    if args.instruments:
        wanted = set(args.instruments.split(","))
        instruments = [i for i in instruments if i.symbol in wanted]

    strategies = all_strategies()
    if args.strategies:
        wanted_strats = set(args.strategies.split(","))
        strategies = [s for s in strategies if s.name in wanted_strats]

    years = args.years or cfg.data.history_backfill_years
    end = datetime.now(UTC)
    start = end - timedelta(days=365 * years)

    per_combo_results: dict[str, dict] = {}
    per_strategy_trades: dict[str, list[pd.DataFrame]] = {s.name: [] for s in strategies}
    p_values: dict[str, float] = {}
    cost_model = CostModel()

    for inst in instruments:
        logger.info("Procesando %s...", inst.symbol)
        try:
            features = build_feature_matrix(
                engine, inst.symbol, Timeframe.H1, start, end, source=cfg.data.provider_backtest,
            )
        except Exception as exc:  # noqa: BLE001 - un instrumento sin datos no debe abortar el resto
            logger.warning("Sin datos utilizables para %s: %s", inst.symbol, exc)
            continue
        if features.empty or len(features) < 200:
            logger.warning("Historico insuficiente para %s (%d filas), se omite", inst.symbol, len(features))
            continue

        try:
            quote_rate = resolve_quote_to_eur_rate(engine, inst.symbol, end, source=cfg.data.provider_backtest)
        except CurrencyConversionError as exc:
            logger.warning(
                "%s: no se pudo resolver tipo de cambio a EUR (%s); se usa 1.0 como aproximacion "
                "(ver limitacion documentada en risk/position_sizing.py)", inst.symbol, exc,
            )
            quote_rate = 1.0

        def _rate_for_ts(_ts, _rate=quote_rate):
            return _rate

        for strategy in strategies:
            combo_key = f"{inst.symbol}::{strategy.name}"
            try:
                signals = strategy.generate_signals(features)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s: fallo generando señales (%s)", combo_key, exc)
                continue

            windows = run_walk_forward(
                inst.symbol, features, signals, cost_model, cfg.account.capital_reference_eur,
                cfg.account.max_risk_per_trade_pct, _rate_for_ts, cfg.backtest.walk_forward_windows,
            )
            if not windows:
                continue

            all_trades = pd.concat([w.trades for w in windows if not w.trades.empty], ignore_index=True) \
                if any(not w.trades.empty for w in windows) else pd.DataFrame()
            oos_metrics = compute_metrics(
                all_trades,
                pd.Series([w.ending_capital_eur for w in windows], index=[w.end for w in windows]),
                cfg.account.capital_reference_eur, windows[0].start, windows[-1].end,
            )

            if oos_metrics.n_trades < MIN_TRADES_TO_EVALUATE:
                per_combo_results[combo_key] = {
                    "instrument": inst.symbol, "strategy": strategy.name,
                    "n_trades": oos_metrics.n_trades, "insufficient_data": True,
                }
                continue

            n_real_signals = len(signals)

            def _random_run(
                seed: int, _features=features, _inst=inst, _n=n_real_signals, _rate=_rate_for_ts
            ) -> BacktestMetrics:
                random_signals = generate_random_baseline_signals(
                    _features, _inst.symbol, Timeframe.H1.value, n_signals=_n, seed=seed,
                )
                if random_signals.empty:
                    return _EMPTY_METRICS
                bt = BacktestEngine(
                    cost_model, cfg.account.capital_reference_eur, cfg.account.max_risk_per_trade_pct
                )
                trades, equity_curve = bt.run(_inst.symbol, _features, random_signals, _rate)
                if not trades:
                    return _EMPTY_METRICS
                return compute_metrics(
                    trades, equity_curve, cfg.account.capital_reference_eur,
                    _features["ts"].min(), _features["ts"].max(),
                )

            baseline_test = random_signal_significance_test(
                oos_metrics, _random_run, n_iterations=N_RANDOM_ITERATIONS, metric="expectancy_eur",
            )
            mc_result = monte_carlo_trade_order(
                all_trades, cfg.account.capital_reference_eur, n_iterations=N_MONTE_CARLO_ITERATIONS,
            )

            p_values[combo_key] = baseline_test.p_value
            per_combo_results[combo_key] = {
                "instrument": inst.symbol, "strategy": strategy.name,
                "metrics": oos_metrics.to_dict(),
                "random_baseline": {
                    "p_value": baseline_test.p_value, "random_mean_expectancy_eur": baseline_test.random_mean,
                    "n_iterations": baseline_test.n_iterations,
                },
                "monte_carlo": mc_result.percentiles,
            }
            per_strategy_trades[strategy.name].append(all_trades)
            logger.info(
                "%s: %d ops, expectancy=%.2f EUR, p_valor_vs_azar=%.3f",
                combo_key, oos_metrics.n_trades, oos_metrics.expectancy_eur, baseline_test.p_value,
            )

    survivors = benjamini_hochberg(p_values, alpha=cfg.backtest.significance_alpha) if p_values else {}

    n_trials_total = len(p_values)
    for combo_key, result in per_combo_results.items():
        if "metrics" not in result:
            continue
        m = result["metrics"]
        result["passes_fdr_correction"] = survivors.get(combo_key, False)
        # pandas .kurt() devuelve exceso de kurtosis (normal=0); la formula del DSR quiere no-exceso (normal=3.0)
        result["deflated_sharpe_probability"] = deflated_sharpe_ratio(
            observed_sharpe=m["sharpe"], n_trials=max(n_trials_total, 1), n_returns=m["n_trades"],
            skew=m["return_skew"], kurtosis=m["return_kurtosis"] + 3.0,
        )

    strategies_summary: dict[str, dict] = {}
    for strategy in strategies:
        combo_keys = [k for k in per_combo_results if k.endswith(f"::{strategy.name}")]
        if not combo_keys:
            strategies_summary[strategy.name] = {
                "discarded": True, "discard_reason": "sin datos suficientes en ningun instrumento",
            }
            continue

        n_survivors = sum(1 for k in combo_keys if survivors.get(k, False))
        trades_list = per_strategy_trades[strategy.name]
        agg_metrics = _aggregate_metrics(
            pd.concat(trades_list, ignore_index=True), cfg.account.capital_reference_eur
        ) if trades_list else None

        strategies_summary[strategy.name] = {
            "n_instruments_tested": len(combo_keys),
            "n_instruments_significant_after_fdr": n_survivors,
            "discarded": n_survivors == 0,
            "discard_reason": None if n_survivors > 0 else (
                f"ningun instrumento supera el test vs. señales aleatorias tras correccion FDR "
                f"(alpha={cfg.backtest.significance_alpha})"
            ),
            "n_trades": agg_metrics.n_trades if agg_metrics else 0,
            "win_rate": round(agg_metrics.win_rate, 4) if agg_metrics else None,
            "expectancy_eur": round(agg_metrics.expectancy_eur, 2) if agg_metrics else None,
            "sharpe": round(agg_metrics.sharpe, 2) if agg_metrics else None,
            "max_drawdown_pct": round(agg_metrics.max_drawdown_pct, 4) if agg_metrics else None,
            "profit_factor": (
                round(agg_metrics.profit_factor, 2)
                if agg_metrics and agg_metrics.profit_factor != float("inf") else
                (agg_metrics.profit_factor if agg_metrics else None)
            ),
            "per_instrument": {
                k.split("::")[0]: v for k, v in per_combo_results.items() if k.endswith(f"::{strategy.name}")
            },
        }

    report = {
        "universe_size": len(instruments),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "walk_forward_windows": cfg.backtest.walk_forward_windows,
        "significance_alpha": cfg.backtest.significance_alpha,
        "n_combinations_tested": n_trials_total,
        "strategies": strategies_summary,
    }
    save_backtest_report(report)
    logger.info("Informe guardado en reports/backtest_summary.json")

    dd_values = [s["max_drawdown_pct"] for s in strategies_summary.values() if s.get("max_drawdown_pct") is not None]
    if dd_values:
        worst_dd_pct = min(dd_values) * 100
        logger.info(
            "Para activar el kill switch de drawdown (risk/kill_switch.py), fija en config.yaml "
            "backtest.reference_max_drawdown_pct en torno a %.1f (el peor drawdown visto aqui). "
            "Ahora mismo esta en null = kill switch de drawdown INACTIVO.", worst_dd_pct,
        )


def _aggregate_metrics(trades_df: pd.DataFrame, capital_ref: float) -> BacktestMetrics:
    """Metricas combinadas de TODOS los instrumentos para una estrategia,
    tratadas como si compartieran una unica cuenta secuencial. Simplificacion
    documentada: cada backtest por instrumento asumio el capital de
    referencia COMPLETO disponible en solitario, asi que esta agregacion
    sobreestima el efecto de composicion respecto a operar de verdad varios
    instrumentos a la vez con el limite de 3 posiciones simultaneas - los
    limites de risk/limits.py SI se aplican en produccion (scheduler/), no
    aqui. Esto es una vista agregada de referencia, no una simulacion de
    cartera conjunta."""
    trades_sorted = trades_df.sort_values("ts_close").reset_index(drop=True)
    equity = capital_ref + trades_sorted["pnl_eur"].cumsum()
    equity_curve = pd.Series(equity.to_numpy(), index=pd.to_datetime(trades_sorted["ts_close"]))
    trades_sorted = trades_sorted.copy()
    trades_sorted["pnl_pct_of_capital"] = trades_sorted["pnl_eur"] / capital_ref
    return compute_metrics(
        trades_sorted, equity_curve, capital_ref, trades_sorted["ts_open"].min(), trades_sorted["ts_close"].max()
    )


if __name__ == "__main__":
    main()
