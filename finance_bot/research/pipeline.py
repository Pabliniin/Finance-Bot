"""Investigacion completa (fase de validacion):

1. Velas, features y candidatos (una sola vez; no dependen del plan de salida).
2. Para cada plan de salida PRE-DECLARADO en config: resultados reales con
   costes, modelo walk-forward con purga -> probabilidades fuera de muestra,
   calibracion de Platt y umbral elegidos SOLO con validacion.
3. Correccion de Benjamini-Hochberg GLOBAL sobre todas las combinaciones
   plan x instrumento x temporalidad: probar varias cosas y quedarse con la
   que mejor sale es exactamente el sesgo que esta correccion penaliza.
4. Solo con --final: evaluacion UNICA del periodo de test reservado con todo
   congelado.
5. Artefactos para el bot en vivo: un modelo por plan (JSON, sin pickle),
   historial fuera de muestra para "casos similares" y models/validation.json
   con que plan (si alguno) usa cada instrumento/temporalidad.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from finance_bot.config import PROJECT_ROOT, AppConfig, ExitPlan, load_config
from finance_bot.data.bars import TF_MINUTES
from finance_bot.data.market import MarketData
from finance_bot.engine.candidates import generate_candidates, model_features, random_candidates
from finance_bot.engine.features import build_features
from finance_bot.engine.labeling import LABEL_COLUMNS, label_candidates
from finance_bot.engine.model import fit, fit_calibration, platt, walk_forward
from finance_bot.logging_setup import setup_logging
from finance_bot.research.evaluation import (
    auc,
    benjamini_hochberg,
    brier,
    calibration_table,
    non_overlapping,
    random_baseline_pvalue,
    summarize,
)
from finance_bot.research.report import write_report

logger = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"
THRESHOLD_GRID = [round(x, 3) for x in np.arange(0.30, 0.801, 0.025)]
MIN_TRADES_THRESHOLD = 100
MIN_TRADES_GROUP = 30
POOL_SIZE = 4000
META_COLUMNS = ["close_time", "symbol", "tf", "direction", "close", "atr", "sl_distance", "triggers"]
HISTORY_COLUMNS = [
    "close_time",
    "symbol",
    "tf",
    "direction",
    "triggers",
    "p_tp1",
    "p_tp2",
    "hit_tp1",
    "hit_tp2",
    "mfe_r",
    "realized_r",
    "r_tp1_only",
    "r_tp2_only",
    "hours_to_exit",
    "hours_to_tp1",
    "exit_time",
    "split",
]


@dataclass
class GroupData:
    symbol: str
    tf: str
    cand: pd.DataFrame  # META + features del modelo
    random: pd.DataFrame  # entradas al azar con el mismo plan de stop
    path: pd.DataFrame  # velas base para resolver resultados
    base_minutes: int


@dataclass
class PlanResult:
    name: str
    plan: ExitPlan
    n_dataset: int
    n_oos: int
    split_counts: dict
    tau: float
    tau_opt: float
    threshold_table: list[dict]
    val_groups: dict
    diagnostics: dict
    test_groups: dict | None = None
    test_extra: dict = field(default_factory=dict)
    val_selected: pd.DataFrame | None = None
    test_selected: pd.DataFrame | None = None


def _intraday_hours(cfg: AppConfig, tf: str) -> list[int] | None:
    return cfg.sessions_utc.allowed_hours if tf in cfg.sessions_utc.intraday_timeframes else None


def build_candidates(cfg: AppConfig, md: MarketData) -> tuple[list[GroupData], dict]:
    symbols = list(cfg.instruments)
    timeframes = list(cfg.timeframes)
    bars = {s: md.all_timeframes(s, timeframes) for s in symbols}
    coverage = {
        s: {tf: (len(b), str(b.index.min()), str(b.index.max())) for tf, b in bars[s].items() if not b.empty}
        for s in symbols
    }
    groups: list[GroupData] = []
    for s_i, symbol in enumerate(symbols):
        other = symbols[1 - s_i] if len(symbols) == 2 else None
        feats = build_features(bars[symbol], bars[other] if other else None)
        h1_path, m1_path = md.base_h1(symbol), md.base_m1(symbol)
        for tf in timeframes:
            if tf not in feats or feats[tf].empty:
                logger.warning("%s %s: sin datos, se omite", symbol, tf)
                continue
            path, base_minutes = (m1_path, 1) if tf == "M15" else (h1_path, 60)
            if path.empty:
                continue
            plan = cfg.timeframes[tf]
            cand = generate_candidates(feats[tf], symbol, tf, plan, _intraday_hours(cfg, tf))
            if cand.empty:
                continue
            block = pd.concat([cand[META_COLUMNS], model_features(cand)], axis=1)
            block.index = pd.Index([f"{symbol}|{tf}|{t.isoformat()}" for t in cand.index])
            rnd = random_candidates(feats[tf], symbol, tf, plan, _intraday_hours(cfg, tf), POOL_SIZE, seed=11 + s_i)
            groups.append(
                GroupData(
                    symbol,
                    tf,
                    block,
                    rnd[["close_time", "symbol", "tf", "direction", "sl_distance"]],
                    path,
                    base_minutes,
                )
            )
            logger.info("%s %s: %d candidatos", symbol, tf, len(block))
    return groups, coverage


def label_plan(cfg: AppConfig, plan: ExitPlan, groups: list[GroupData]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, pool = [], []
    for g in groups:
        inst = cfg.instrument(g.symbol)
        args = (
            g.path,
            inst,
            TF_MINUTES[g.tf],
            plan.max_bars(cfg.timeframes[g.tf]),
            plan.targets(),
            plan.partial,
            g.base_minutes,
        )
        labels = label_candidates(g.cand, *args)
        rows.append(pd.concat([g.cand, labels], axis=1)[labels["complete"]])
        rnd_labels = label_candidates(g.random, *args)
        pool.append(pd.concat([g.random, rnd_labels], axis=1)[rnd_labels["complete"]])
    dataset = pd.concat(rows)
    dataset["exit_time"] = pd.to_datetime(dataset["exit_time"], utc=True)
    return dataset, pd.concat(pool)


def _validation_end(cfg: AppConfig, tf: str) -> pd.Timestamp:
    raw = cfg.research.validation_end_overrides.get(tf, cfg.research.validation_end)
    return pd.Timestamp(raw, tz="UTC") + pd.Timedelta(days=1)


def group_key(symbol: str, tf: str) -> str:
    return f"{symbol} {tf}"


def select_threshold(val: pd.DataFrame) -> tuple[float, list[dict]]:
    """Unico parametro libre por plan, elegido SOLO con validacion: el umbral
    que maximiza el t-estadistico de la expectativa (no la expectativa bruta:
    premiar pocos casos con suerte es sobreajuste)."""
    table = []
    best_tau, best_t = THRESHOLD_GRID[0], -np.inf
    for tau in THRESHOLD_GRID:
        s = summarize(non_overlapping(val[val["p_tp1"] >= tau]))
        table.append({"threshold": tau, **s.to_dict()})
        if s.n >= MIN_TRADES_THRESHOLD and np.isfinite(s.t_stat) and s.t_stat > best_t:
            best_tau, best_t = tau, s.t_stat
    return best_tau, table


def evaluate_groups(
    rows: pd.DataFrame, pool: pd.DataFrame, tau: float, iterations: int
) -> tuple[dict[str, dict], pd.DataFrame]:
    out = {}
    selected = non_overlapping(rows[rows["p_tp1"] >= tau])
    for (symbol, tf), grp in rows.groupby(["symbol", "tf"]):
        trades = selected[(selected["symbol"] == symbol) & (selected["tf"] == tf)]
        s = summarize(trades)
        start, end = grp["close_time"].min(), grp["close_time"].max()
        pool_grp = pool[(pool["symbol"] == symbol) & (pool["tf"] == tf) & pool["close_time"].between(start, end)]
        p_rand, mean_rand = random_baseline_pvalue(s.mean_r, s.n, pool_grp["realized_r"].to_numpy(), iterations)
        out[group_key(symbol, tf)] = {
            "summary": s.to_dict(),
            "p_value_vs_random": p_rand,
            "random_mean_r": mean_rand,
            "unfiltered": summarize(non_overlapping(grp)).to_dict(),
        }
    return out, selected


def trigger_breakdown(rows: pd.DataFrame) -> list[dict]:
    out = []
    for trig, grp in rows.explode("triggers").groupby("triggers"):
        s = summarize(grp)
        out.append(
            {
                "trigger": trig,
                "n": s.n,
                "tp1_rate": s.tp1_rate,
                "mean_r": s.mean_r,
                "p_value_vs_zero": s.p_value_vs_zero,
            }
        )
    return sorted(out, key=lambda d: -(d["mean_r"] if d["mean_r"] is not None else -9))


def research_plan(
    cfg: AppConfig, name: str, plan: ExitPlan, groups: list[GroupData], final: bool, started: datetime
) -> PlanResult:
    logger.info(
        "=== Plan %s: TP %s, parcial %.0f%%, tiempo x%.1f ===",
        name,
        plan.targets_r,
        100 * plan.partial,
        plan.max_bars_mult,
    )
    dataset, pool = label_plan(cfg, plan, groups)
    features = [c for c in dataset.columns if c not in META_COLUMNS and c not in LABEL_COLUMNS]

    first_test = pd.Timestamp(f"{cfg.research.first_test_year}-01-01", tz="UTC")
    oos = walk_forward(dataset, features, first_test, cfg.research.retrain_every_months)
    data = dataset.join(oos, how="inner")

    val_end = data["tf"].map(lambda tf: _validation_end(cfg, tf))
    data["split"] = np.where(
        data["exit_time"] < val_end, "validation", np.where(data["close_time"] >= val_end, "test", "gap")
    )
    val = data[data["split"] == "validation"].copy()

    calib1 = fit_calibration(val["p_tp1_raw"].to_numpy(), val["hit_tp1"].to_numpy())
    calib2 = fit_calibration(val["p_tp2_raw"].to_numpy(), val["hit_tp2"].to_numpy())
    data["p_tp1"] = platt(data["p_tp1_raw"].to_numpy(), *calib1)
    data["p_tp2"] = np.minimum(platt(data["p_tp2_raw"].to_numpy(), *calib2), data["p_tp1"])
    val = data[data["split"] == "validation"].copy()
    test = data[data["split"] == "test"].copy()

    tau_opt, threshold_table = select_threshold(val)
    tau = tau_opt  # el suelo de config.signals es un filtro extra en vivo, no altera la validacion
    val_groups, val_selected = evaluate_groups(val, pool, tau, cfg.research.random_baseline_iterations)

    diagnostics = {
        "auc_tp1": auc(val["p_tp1_raw"].to_numpy(), val["hit_tp1"].to_numpy()),
        "brier_model": brier(val["p_tp1"].to_numpy(), val["hit_tp1"].to_numpy()),
        "brier_base_rate": brier(np.full(len(val), val["hit_tp1"].mean()), val["hit_tp1"].to_numpy()),
        # SIN recalibrar: la de Platt se ajusta con estos datos (juzgarla aqui seria su propio examen)
        "calibration": calibration_table(val["p_tp1_raw"].to_numpy(), val["hit_tp1"].to_numpy()).to_dict("records"),
        "calibration_params": {"tp1": calib1, "tp2": calib2},
        "p_tp1_quantiles": {str(k): v for k, v in val["p_tp1"].quantile([0.1, 0.5, 0.9, 0.99]).round(3).items()},
        "base_tp1_rate": float(val["hit_tp1"].mean()),
        "triggers": trigger_breakdown(val),
    }

    result = PlanResult(
        name,
        plan,
        len(dataset),
        len(data),
        data["split"].value_counts().to_dict(),
        tau,
        tau_opt,
        threshold_table,
        val_groups,
        diagnostics,
        val_selected=val_selected,
    )

    if final:
        test_groups, test_selected = evaluate_groups(test, pool, tau, cfg.research.random_baseline_iterations)
        result.test_groups = test_groups
        result.test_selected = test_selected
        result.test_extra = {
            "auc_tp1": auc(test["p_tp1_raw"].to_numpy(), test["hit_tp1"].to_numpy()),
            "brier_model": brier(test["p_tp1"].to_numpy(), test["hit_tp1"].to_numpy()),
            "brier_base_rate": brier(np.full(len(test), val["hit_tp1"].mean()), test["hit_tp1"].to_numpy()),
            "calibration": calibration_table(test["p_tp1"].to_numpy(), test["hit_tp1"].to_numpy()).to_dict("records"),
            "unfiltered_pooled": summarize(non_overlapping(test)).to_dict(),
        }

    # Modelo para el bot en vivo: todo el historico con resultado. La calibracion es
    # la MISMA de validacion: la probabilidad en vivo, el umbral elegido y el
    # historial de casos similares tienen que estar en la misma escala (recalibrar
    # con el test desplazaba la escala en vivo hasta ~2 pp y aflojaba el umbral).
    live = fit(dataset[features], dataset["hit_tp1"], dataset["hit_tp2"])
    live.tp1.calib_a, live.tp1.calib_b = calib1
    live.tp2.calib_a, live.tp2.calib_b = calib2
    # Casos similares: validacion y, tras la evaluacion final, tambien el test
    history_rows = data[data["split"].isin(["validation", "test"] if final else ["validation"])]
    live.metadata = {
        "plan": name,
        "plan_params": plan.model_dump(),
        "trained_at": started.isoformat(),
        "training_rows": len(dataset),
        "training_period": [str(dataset["close_time"].min()), str(dataset["close_time"].max())],
        "threshold_tp1": tau,
        "test_evaluated": final,
    }
    plan_dir = MODELS_DIR / name
    live.to_json(plan_dir / "model.json")
    history_rows[HISTORY_COLUMNS].to_parquet(plan_dir / "oos_history.parquet")
    result.diagnostics["coefficients"] = sorted(
        zip(live.feature_names, live.tp1.coef, strict=True), key=lambda kv: -abs(kv[1])
    )
    return result


def run_research(final: bool = False) -> int:
    setup_logging("research")
    cfg = load_config()
    started = datetime.now(UTC)
    groups, coverage = build_candidates(cfg, MarketData(cfg))

    results = [research_plan(cfg, name, plan, groups, final, started) for name, plan in cfg.plans.items()]

    # --- correccion global y seleccion de plan por grupo (solo validacion) ---
    p_values = {}
    for r in results:
        for key, info in r.val_groups.items():
            s = info["summary"]
            if s["n"] >= MIN_TRADES_GROUP and s["p_value_vs_zero"] is not None:
                p_values[f"{r.name}|{key}"] = s["p_value_vs_zero"]
    bh = benjamini_hochberg(p_values, cfg.research.significance_alpha)
    for r in results:
        for key, info in r.val_groups.items():
            s = info["summary"]
            info["passes_bh"] = bool(bh.get(f"{r.name}|{key}", False))
            info["eligible"] = bool(info["passes_bh"] and s["n"] >= MIN_TRADES_GROUP and (s["mean_r"] or 0) > 0)

    selection: dict[str, str] = {}
    for key in sorted({k for r in results for k in r.val_groups}):
        candidates = [
            (r.val_groups[key]["summary"]["t_stat"] or 0, r.name)
            for r in results
            if key in r.val_groups and r.val_groups[key]["eligible"]
        ]
        if candidates:
            selection[key] = max(candidates)[1]

    test_summary = None
    if final:
        by_name = {r.name: r for r in results}
        chosen = []
        for key, plan_name in selection.items():
            sel = by_name[plan_name].test_selected
            if sel is None:  # no ocurre con final=True, pero nunca se indexa un None
                continue
            symbol, tf = key.split(" ")
            chosen.append(sel[(sel["symbol"] == symbol) & (sel["tf"] == tf)])
        pooled = (
            pd.concat(chosen)
            if chosen
            else pd.DataFrame(columns=["close_time", "realized_r", "hit_tp1", "hit_tp2", "hours_to_exit"])
        )
        test_summary = {"selected_pooled": summarize(pooled).to_dict() if not pooled.empty else None}

    validation_payload = {
        "generated_at": started.isoformat(),
        "test_evaluated": final,
        "selection": selection,
        "plans": {
            r.name: {
                "threshold_tp1": r.tau,
                "threshold_optimal_validation": r.tau_opt,
                "params": r.plan.model_dump(),
                "groups": r.val_groups,
            }
            for r in results
        },
    }
    MODELS_DIR.mkdir(exist_ok=True)
    (MODELS_DIR / "validation.json").write_text(json.dumps(validation_payload, indent=1, default=str), encoding="utf-8")

    path = write_report(
        REPORTS_DIR,
        started=started,
        final=final,
        coverage=coverage,
        results=results,
        selection=selection,
        test_summary=test_summary,
        n_tests=len(p_values),
        cfg=cfg,
    )
    logger.info("Informe: %s", path)
    print(f"\nInforme escrito en {path}")
    return 0
