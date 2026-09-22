"""Evaluacion honesta de los resultados fuera de muestra.

- Las operaciones de un mismo instrumento y temporalidad no se solapan: si
  hay una abierta, las señales siguientes se ignoran hasta que se cierra
  (asi lo viviria un trader, y asi no se cuentan dos veces las mismas velas).
- La expectativa se contrasta contra 0 (¿gana dinero despues de costes?) y
  contra entradas ALEATORIAS con el mismo plan (¿la seleccion aporta algo o
  cualquier entrada habria dado lo mismo?).
- Con varios grupos probados a la vez se corrige por comparaciones multiples
  (Benjamini-Hochberg): probar 8 cosas y quedarse con la que sale bien es la
  forma mas comun de engañarse con un backtest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats

from finance_bot.engine.labeling import ns


def non_overlapping(trades: pd.DataFrame, group_cols: tuple[str, ...] = ("symbol", "tf")) -> pd.DataFrame:
    """Solo las señales que se podrian haber tomado sin tener otra abierta en
    el mismo grupo. Trabaja por POSICION (no por etiqueta de indice), asi
    que funciona aunque el indice tenga duplicados."""
    if trades.empty:
        return trades
    ordered = trades.sort_values("close_time", kind="stable")
    opens = ns(ordered["close_time"])
    exits = ns(ordered["exit_time"])
    keys = list(zip(*(ordered[c].to_numpy() for c in group_cols), strict=True))
    keep = np.zeros(len(ordered), dtype=bool)
    busy_until: dict[tuple, int] = {}
    for i, key in enumerate(keys):
        if opens[i] >= busy_until.get(key, np.iinfo(np.int64).min):
            keep[i] = True
            busy_until[key] = exits[i]
    return ordered[keep]


def wilson_interval(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (float(center - half), float(center + half))


@dataclass
class Summary:
    n: int
    tp1_rate: float
    tp1_ci_low: float
    tp1_ci_high: float
    tp2_rate: float
    win_rate: float
    mean_r: float
    median_r: float
    total_r: float
    se_r: float
    t_stat: float
    p_value_vs_zero: float
    profit_factor: float
    max_drawdown_r: float
    median_hours: float
    trades_per_year: float

    def to_dict(self) -> dict:
        return {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in asdict(self).items()}


def summarize(trades: pd.DataFrame, r_col: str = "realized_r") -> Summary:
    n = len(trades)
    if n == 0:
        nan = float("nan")
        return Summary(0, nan, nan, nan, nan, nan, nan, nan, 0.0, nan, nan, nan, nan, nan, nan, 0.0)
    r = trades[r_col].to_numpy(dtype="float64")
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    equity = np.cumsum(r)
    drawdown = float((np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:] - equity).max())
    se = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    t = float(r.mean() / se) if n > 1 and se > 0 else float("nan")
    p = float(stats.t.sf(t, df=n - 1)) if np.isfinite(t) else float("nan")
    years = max((trades["close_time"].max() - trades["close_time"].min()).days / 365.25, 1 / 12)
    lo, hi = wilson_interval(trades["hit_tp1"].sum(), n)
    return Summary(
        n=n,
        tp1_rate=float(trades["hit_tp1"].mean()),
        tp1_ci_low=lo,
        tp1_ci_high=hi,
        tp2_rate=float(trades["hit_tp2"].mean()),
        win_rate=float((r > 0).mean()),
        mean_r=float(r.mean()),
        median_r=float(np.median(r)),
        total_r=float(r.sum()),
        se_r=se,
        t_stat=t,
        p_value_vs_zero=p,
        profit_factor=float(wins / losses) if losses > 0 else float("inf"),
        max_drawdown_r=drawdown,
        median_hours=float(trades["hours_to_exit"].median()),
        trades_per_year=float(n / years),
    )


def random_baseline_pvalue(
    observed_mean: float, n: int, pool_r: np.ndarray, iterations: int, seed: int = 7
) -> tuple[float, float]:
    """Proporcion de muestras aleatorias (del pool de entradas al azar con el
    mismo plan) cuya media iguala o supera la observada. Devuelve (p, media
    del azar)."""
    if n == 0 or len(pool_r) < n:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.array([pool_r[rng.choice(len(pool_r), size=n, replace=False)].mean() for _ in range(iterations)])
    return float((means >= observed_mean).mean()), float(means.mean())


def benjamini_hochberg(p_values: dict[str, float], alpha: float) -> dict[str, bool]:
    items = sorted(((k, v) for k, v in p_values.items() if np.isfinite(v)), key=lambda kv: kv[1])
    m = len(items)
    cutoff = 0
    for rank, (_, p) in enumerate(items, start=1):
        if p <= rank / m * alpha:
            cutoff = rank
    passed = {k: rank <= cutoff for rank, (k, _) in enumerate(items, start=1)}
    return {k: passed.get(k, False) for k in p_values}


def calibration_table(
    p: np.ndarray, y: np.ndarray, bins: tuple[float, ...] = (0, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 1.0)
) -> pd.DataFrame:
    df = pd.DataFrame({"p": p, "y": y})
    df["bin"] = pd.cut(df["p"], bins=list(bins), include_lowest=True)
    table = df.groupby("bin", observed=True).agg(n=("y", "size"), predicho=("p", "mean"), observado=("y", "mean"))
    return table.reset_index().assign(bin=lambda t: t["bin"].astype(str))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def auc(p: np.ndarray, y: np.ndarray) -> float:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    ranks = stats.rankdata(np.concatenate([pos, neg]))
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))
