"""Informe de investigacion en Markdown (español). Muestra TODO: lo que
funciona, lo que no y lo que se descarto, sin maquillaje."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from finance_bot.config import AppConfig
from finance_bot.strategies.labels import feature_label
from finance_bot.strategies.triggers import TRIGGER_NAMES

if TYPE_CHECKING:
    from finance_bot.research.pipeline import PlanResult


def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def _num(x, digits: int = 3) -> str:
    return "—" if x is None else f"{x:+.{digits}f}"


def _p(x) -> str:
    if x is None or x != x:
        return "—"
    return "<0.001" if x < 0.001 else f"{x:.3f}"


def _group_rows(groups: dict, flags: bool) -> list[str]:
    header = (
        "| Grupo | Señales | Acierto TP1 [IC95] | Acierto TP2 | Expectativa R/op "
        "| p vs 0 | p vs azar | R/op sin filtrar |"
    )
    sep = "|---|---:|---|---:|---:|---:|---:|---:|"
    if flags:
        header += " BH | Apto |"
        sep += "---|---|"
    lines = [header, sep]
    for key, info in sorted(groups.items()):
        s, u = info["summary"], info["unfiltered"]
        ci = f"{_pct(s['tp1_rate'])} [{_pct(s['tp1_ci_low'])}–{_pct(s['tp1_ci_high'])}]" if s["n"] else "—"
        line = (
            f"| {key} | {s['n']} | {ci} | {_pct(s['tp2_rate'])} | {_num(s['mean_r'])} | {_p(s['p_value_vs_zero'])} "
            f"| {_p(info.get('p_value_vs_random'))} | {_num(u['mean_r'])} |"
        )
        if flags:
            line += f" {'sí' if info.get('passes_bh') else 'no'} | {'✅' if info.get('eligible') else '❌'} |"
        lines.append(line)
    return lines


def _calibration_rows(rows: list[dict]) -> list[str]:
    out = ["| Probabilidad | Casos | Predicho medio | Ocurrido |", "|---|---:|---:|---:|"]
    out += [f"| {r['bin']} | {r['n']:,} | {_pct(r['predicho'])} | {_pct(r['observado'])} |" for r in rows]
    return out


def write_report(
    reports_dir: Path,
    *,
    started: datetime,
    final: bool,
    coverage: dict,
    results: list[PlanResult],
    selection: dict[str, str],
    test_summary: dict | None,
    n_tests: int,
    cfg: AppConfig,
) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    L.append(f"# Informe de validación — {started:%Y-%m-%d %H:%M} UTC")
    L.append("")
    L.append(
        "Todo es **fuera de muestra** (cada probabilidad la calculó un modelo que no había visto ese periodo), "
        "con costes (spread, deslizamiento, swap). R = lo que arriesgas por operación."
    )
    L.append("")
    L.append(f"- Test reservado evaluado: **{'SÍ — ejecución final única' if final else 'NO — solo validación'}**")
    alpha = cfg.research.significance_alpha
    L.append(f"- Combinaciones plan × grupo contrastadas (corrección BH global, α={alpha}): {n_tests}")
    L.append("")
    L.append("## 1. Datos")
    L.append("")
    L.append("| Instrumento | Temporalidad | Velas | Desde | Hasta |")
    L.append("|---|---|---:|---|---|")
    for sym, tfs in coverage.items():
        for tf, (n, start, end) in tfs.items():
            L.append(f"| {sym} | {tf} | {n:,} | {start[:10]} | {end[:10]} |")
    L.append("")

    L.append("## 2. Resumen por plan (validación)")
    L.append("")
    L.append("| Plan | TP1 / TP2 | Acierto TP1 base | AUC | Umbral | Señales | Acierto TP1 | Expectativa R/op | t |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        best = next((row for row in r.threshold_table if row["threshold"] == r.tau), {})
        t = best.get("t_stat")
        L.append(
            f"| {r.name} | {r.plan.targets_r[0]}R / {r.plan.targets_r[1]}R | {_pct(r.diagnostics['base_tp1_rate'])} "
            f"| {r.diagnostics['auc_tp1']:.3f} | {_pct(r.tau)} | {best.get('n', 0)} | {_pct(best.get('tp1_rate'))} "
            f"| {_num(best.get('mean_r'))} | {'—' if t is None else f'{t:.2f}'} |"
        )
    L.append("")
    L.append(
        "Para no perder dinero con un plan, el acierto debe superar el de equilibrio: "
        "con TP a X·R y stop a 1R hace falta acertar más de 1/(1+X) antes de costes "
        "(0.5R → 66.7%, 1R → 50%, 1.5R → 40%)."
    )
    L.append("")

    for r in results:
        d = r.diagnostics
        t1, t2 = r.plan.targets_r
        L.append(f"## Plan «{r.name}» — TP1 {t1}R, TP2 {t2}R, cierre por tiempo ×{r.plan.max_bars_mult}")
        L.append("")
        L.append(
            f"- Candidatos con resultado: {r.n_dataset:,}; con predicción fuera de muestra: {r.n_oos:,} "
            f"({', '.join(f'{k}: {v:,}' for k, v in r.split_counts.items())})"
        )
        L.append(
            f"- AUC TP1 {d['auc_tp1']:.3f} · Brier {d['brier_model']:.4f} vs {d['brier_base_rate']:.4f} sin modelo "
            f"· probabilidad TP1 calibrada p10/p50/p90/p99: "
            + " / ".join(_pct(v) for v in d["p_tp1_quantiles"].values())
        )
        L.append("")
        L.append(
            "Calibración en validación (probabilidad del walk-forward SIN recalibrar; la recalibración de Platt se "
            "ajusta con estos datos y su examen real es el test):"
        )
        L.append("")
        L.extend(_calibration_rows(d["calibration"]))
        L.append("")
        L.append("Estrategias de entrada por sí solas (validación, sin filtrar por el modelo):")
        L.append("")
        L.append("| Estrategia | Señales | Acierto TP1 | Expectativa R/op | p vs 0 |")
        L.append("|---|---:|---:|---:|---:|")
        for t in d["triggers"]:
            name = TRIGGER_NAMES.get(t["trigger"], t["trigger"])
            L.append(
                f"| {name} | {t['n']:,} | {_pct(t['tp1_rate'])} | {_num(t['mean_r'])} | {_p(t['p_value_vs_zero'])} |"
            )
        L.append("")
        L.append("Umbral de probabilidad (validación):")
        L.append("")
        L.append("| Umbral | Señales | Acierto TP1 | Expectativa R/op | t | Señales/año |")
        L.append("|---:|---:|---:|---:|---:|---:|")
        for row in r.threshold_table:
            if row["n"] == 0:
                continue
            t = row.get("t_stat")
            marker = " ◀" if row["threshold"] == r.tau else ""
            t_text = "—" if t is None else f"{t:.2f}"
            L.append(
                f"| {_pct(row['threshold'])}{marker} | {row['n']} | {_pct(row['tp1_rate'])} "
                f"| {_num(row['mean_r'])} | {t_text} | {row['trades_per_year']:.0f} |"
            )
        L.append("")
        L.append("Grupos en validación con el umbral elegido:")
        L.append("")
        L.extend(_group_rows(r.val_groups, flags=True))
        L.append("")
        if final and r.test_groups is not None:
            e = r.test_extra
            L.append(
                f"**Test reservado** — AUC {e['auc_tp1']:.3f} · "
                f"Brier {e['brier_model']:.4f} vs {e['brier_base_rate']:.4f}"
            )
            L.append("")
            L.extend(_group_rows(r.test_groups, flags=False))
            L.append("")
            L.append("Calibración en test (aquí sí con la recalibración, que no vio estos datos):")
            L.append("")
            L.extend(_calibration_rows(e["calibration"]))
            L.append("")

    first = results[0]
    L.append(f"## Qué aporta cada factor a la confluencia (modelo final, plan «{first.name}»)")
    L.append("")
    L.append(
        "Coeficientes estandarizados del modelo de TP1: positivo = sube la probabilidad cuando está a favor. "
        "Muchas estrategias miden casi lo mismo; un coeficiente ~0 significa que no añade nada que no digan ya otras."
    )
    L.append("")
    L.append("| Factor | Coeficiente |")
    L.append("|---|---:|")
    for name, coef in first.diagnostics["coefficients"][:25]:
        L.append(f"| {feature_label(name)} | {coef:+.3f} |")
    L.append("")

    L.append("## Conclusión")
    L.append("")
    if selection:
        L.append("Combinaciones con ventaja estadística en validación (tras corrección global):")
        L.append("")
        for key, plan_name in sorted(selection.items()):
            L.append(f"- **{key}** → plan «{plan_name}»")
        L.append("")
        L.append("En modo estricto el bot solo emite señales de estas combinaciones.")
    else:
        L.append(
            "**Ninguna combinación de plan, instrumento y temporalidad supera la validación con corrección por "
            "comparaciones múltiples.** En modo estricto el bot no emitirá señales de entrada: mostrará el "
            "análisis completo y la probabilidad estimada, pero no te dirá que entres. Es el resultado honesto."
        )
    if final and test_summary is not None:
        s = test_summary.get("selected_pooled")
        L.append("")
        if s:
            L.append(
                f"Resultado en el test reservado de las combinaciones seleccionadas: {s['n']} señales, "
                f"acierto TP1 {_pct(s['tp1_rate'])}, expectativa {_num(s['mean_r'])} R/op "
                f"(p vs 0 = {_p(s['p_value_vs_zero'])}), drawdown máximo {s['max_drawdown_r']:.1f}R."
            )
        else:
            L.append("Sin combinaciones seleccionadas, no hay nada que evaluar en el test.")
    L.append("")
    L.append("> " + cfg.disclaimer)

    text = "\n".join(L)
    path = reports_dir / f"research_{started:%Y%m%d_%H%M}.md"
    path.write_text(text, encoding="utf-8")
    (reports_dir / "latest.md").write_text(text, encoding="utf-8")
    (reports_dir / "latest.json").write_text(
        json.dumps(
            {
                "selection": selection,
                "final": final,
                "plans": {
                    r.name: {"tau": r.tau, "val_groups": r.val_groups, "test_groups": r.test_groups} for r in results
                },
            },
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    return path
