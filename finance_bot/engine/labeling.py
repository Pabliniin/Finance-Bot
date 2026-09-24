"""Etiquetado de resultados ("triple barrera": stop, objetivo, tiempo) sobre el
camino real del precio posterior a cada candidato, con costes.

Reglas (identicas a las del seguimiento en vivo):
- Precios BID. Un largo compra al ASK (bid + spread) y cierra al BID; un corto
  vende al BID y recompra al ASK. Asi el spread se paga siempre, entrando y
  saliendo por el lado correcto.
- Entrada: apertura de la primera vela base posterior al cierre de la señal
  (+ deslizamiento). Si hay hueco de fin de semana, se entra con el hueco.
- Stop: se ejecuta al nivel o PEOR si la vela abre ya mas alla (hueco), mas
  deslizamiento. Objetivos: ordenes limite, al nivel exacto.
- Si stop y objetivo caben en la misma vela base, se asume el STOP primero
  (pesimista: la vela no dice que ocurrio antes).
- Plan de gestion: se cierra `partial` en TP1 y el resto va a TP2 con el stop
  movido a break-even. Cierre por tiempo a las max_bars velas de la señal.
- Swap: por cada rollover (17:00 Nueva York, lunes a viernes; x3 el miercoles).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from finance_bot.config import InstrumentConfig

_NY = "America/New_York"


def ns(values: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """Enteros en NANOSEGUNDOS, sea cual sea la resolucion interna. pandas 3
    ya no garantiza [ns] (puede inferir [s] o [us]) y `asi8` devuelve la
    unidad interna: mezclar unidades rompe silenciosamente toda la aritmetica
    de tiempos del etiquetado."""
    return pd.DatetimeIndex(values).as_unit("ns").asi8


def rollover_schedule(start: pd.Timestamp, end: pd.Timestamp) -> tuple[np.ndarray, np.ndarray]:
    """Instantes de rollover (UTC, ns) y peso acumulado (miercoles = 3)."""
    days = pd.date_range(
        start.tz_convert(_NY).normalize() - pd.Timedelta(days=1),
        end.tz_convert(_NY).normalize() + pd.Timedelta(days=1),
        freq="D",
    )
    days = days[days.dayofweek < 5]
    stamps = (days + pd.Timedelta(hours=17)).tz_convert("UTC")
    weights = np.where(days.dayofweek == 2, 3.0, 1.0)
    return ns(stamps), np.concatenate([[0.0], np.cumsum(weights)])


def _nights(times_ns: np.ndarray, cumw: np.ndarray, a_ns: int, b_ns: int) -> float:
    ia = np.searchsorted(times_ns, a_ns, side="right")
    ib = np.searchsorted(times_ns, b_ns, side="right")
    return float(cumw[ib] - cumw[ia])


def _first(mask: np.ndarray) -> int:
    idx = np.flatnonzero(mask)
    return int(idx[0]) if idx.size else len(mask)


def _single_target(k_target: int, k_sl: int, n: int, target: float, stop_r: float, mtm_r: float) -> tuple[float, int]:
    """(R, indice de salida) de una operacion con UN solo objetivo."""
    if k_target < k_sl and k_target < n:
        return target, k_target
    if k_sl < n:
        return stop_r, k_sl
    return mtm_r, n - 1


LABEL_COLUMNS = [
    "complete",
    "entry_time",
    "entry_price",
    "r_price",
    "spread_price",
    "hit_tp1",
    "hit_tp2",
    "exit_reason",
    "exit_time",
    "hours_to_exit",
    "hours_to_tp1",
    "mfe_r",
    "mae_r",
    "realized_r",
    "r_tp1_only",
    "r_tp2_only",
    "swap_r",
]


def label_candidates(
    cand: pd.DataFrame,
    path: pd.DataFrame,
    inst: InstrumentConfig,
    tf_minutes: int,
    max_bars: int,
    targets_r: tuple[float, float],
    partial: float,
    base_minutes: int,
) -> pd.DataFrame:
    """Devuelve una fila de resultados por candidato (mismo indice). Los que
    no tienen suficiente futuro para resolverse quedan con complete=False."""
    out = pd.DataFrame(index=cand.index, columns=LABEL_COLUMNS, dtype="object")
    out["complete"] = False
    if cand.empty or path.empty:
        return out

    times = ns(path.index)
    op, hi, lo, cl = (path[c].to_numpy(dtype="float64") for c in ("open", "high", "low", "close"))
    data_end_ns = times[-1] + base_minutes * 60_000_000_000
    roll_ns, roll_cumw = rollover_schedule(path.index[0], path.index[-1] + pd.Timedelta(days=30))
    t1, t2 = targets_r
    base_ns = base_minutes * 60_000_000_000
    hour_ns = 3_600_000_000_000

    swap_long, swap_short = inst.swap_per_night.long, inst.swap_per_night.short
    signal_ns = ns(cand["close_time"])
    directions = cand["direction"].to_numpy()
    r_prices = cand["sl_distance"].to_numpy(dtype="float64")
    # En vivo se sigue la señal con el precio de entrada que se ENVIO al usuario.
    entry_overrides = cand["entry_override"].to_numpy(dtype="float64") if "entry_override" in cand else None
    limit_step = max_bars * tf_minutes * 60_000_000_000

    records: list[tuple | None] = []
    for pos in range(len(cand)):
        t0 = int(signal_ns[pos])
        d = int(directions[pos])
        r = float(r_prices[pos])
        limit = t0 + limit_step
        i0 = int(np.searchsorted(times, t0, side="left"))
        i1 = int(np.searchsorted(times, limit, side="left"))
        if i0 >= len(times) or i1 <= i0 or not np.isfinite(r) or r <= 0:
            records.append(None)
            continue

        bid0 = op[i0]
        spread = inst.spread.price_units(bid0)
        slip = inst.slippage.price_units(bid0)
        if entry_overrides is not None and np.isfinite(entry_overrides[pos]):
            entry = float(entry_overrides[pos])
        else:
            entry = bid0 + spread + slip if d > 0 else bid0 - slip
        sl = entry - d * r
        tp1 = entry + d * r * t1
        tp2 = entry + d * r * t2

        h, lw, o, c = hi[i0:i1], lo[i0:i1], op[i0:i1], cl[i0:i1]
        if d > 0:
            sl_hit, tp1_hit, tp2_hit = lw <= sl, h >= tp1, h >= tp2
            excursion = h - entry
            adverse = entry - lw
            be_hit = lw <= entry
        else:
            sl_hit, tp1_hit, tp2_hit = h + spread >= sl, lw + spread <= tp1, lw + spread <= tp2
            excursion = entry - (lw + spread)
            adverse = (h + spread) - entry
            be_hit = h + spread >= entry

        n = len(h)
        k_sl, k1, k2 = _first(sl_hit), _first(tp1_hit), _first(tp2_hit)
        hit1 = k1 < k_sl and k1 < n
        hit2 = k2 < k_sl and k2 < n
        window_complete = limit <= data_end_ns

        mfe_end = k_sl if k_sl < n else n
        mfe_r = float(max(excursion[:mfe_end].max(initial=0.0), 0.0) / r)
        # Hasta e INCLUYENDO la vela donde toca TP1: si toca en la vela de entrada
        # (k1 = 0), su excursion adversa cuenta igual (antes se registraba 0).
        mae_end = (k1 + 1) if hit1 else n
        mae_r = float(min(adverse[:mae_end].max(initial=0.0) / r, 1.0 + slip / r))

        # Resultado si salta el stop (al nivel o peor si la vela abre mas alla, mas
        # deslizamiento) y resultado a precio de mercado al final de la ventana.
        if k_sl < n:
            fill = (min(sl, o[k_sl]) - slip) if d > 0 else (max(sl, o[k_sl] + spread) + slip)
            stop_r = d * (fill - entry) / r
        else:
            stop_r = float("nan")
        mtm_r = d * ((c[-1] if d > 0 else c[-1] + spread) - entry) / r

        # plan con cierre parcial en TP1 y break-even para el resto
        if hit1:
            gain = partial * t1
            k2r = k1 + _first(tp2_hit[k1:])
            kbe = k1 + 1 + _first(be_hit[k1 + 1 :]) if k1 + 1 < n else n
            if k2r < n and k2r < kbe:
                gain += (1 - partial) * t2
                exit_k, reason = k2r, "tp2"
            elif kbe < n:
                gain += (1 - partial) * (-slip / r)
                exit_k, reason = kbe, "tp1_be"
            else:
                gain += (1 - partial) * mtm_r
                exit_k, reason = n - 1, "tp1_time"
            tp1_ns = int(times[i0 + k1]) + base_ns
        elif k_sl < n:
            gain = stop_r
            exit_k, reason = k_sl, "stop"
            tp1_ns = None
        else:
            gain = mtm_r
            exit_k, reason = n - 1, "time"
            tp1_ns = None

        exit_ns = int(times[i0 + exit_k]) + base_ns
        entry_ns = int(times[i0])
        swap = swap_long if d > 0 else swap_short
        if tp1_ns is not None:
            nights_full = _nights(roll_ns, roll_cumw, entry_ns, min(tp1_ns, exit_ns))
            nights_rest = _nights(roll_ns, roll_cumw, min(tp1_ns, exit_ns), exit_ns)
            swap_r = (nights_full + (1 - partial) * nights_rest) * swap / r
        else:
            swap_r = _nights(roll_ns, roll_cumw, entry_ns, exit_ns) * swap / r

        # variantes de un solo objetivo (para el informe)
        r1, e1 = _single_target(k1, k_sl, n, t1, stop_r, mtm_r)
        r2, e2 = _single_target(k2, k_sl, n, t2, stop_r, mtm_r)
        swap1 = _nights(roll_ns, roll_cumw, entry_ns, int(times[i0 + e1]) + base_ns) * swap / r
        swap2 = _nights(roll_ns, roll_cumw, entry_ns, int(times[i0 + e2]) + base_ns) * swap / r

        # Cerrada si toco stop, TP2 o break-even, o si ya paso todo su tiempo. Si
        # no, sigue ABIERTA (en vivo: TP1 ya tocado pero el resto en curso): los
        # campos describen el estado "hasta ahora" y la investigacion la ignora.
        closed = window_complete or reason in ("stop", "tp2", "tp1_be")
        records.append(
            (
                closed,
                pd.Timestamp(entry_ns, tz="UTC"),
                entry,
                r,
                spread,
                float(hit1),
                float(hit2),
                reason,
                pd.Timestamp(exit_ns, tz="UTC"),
                (exit_ns - t0) / hour_ns,
                (tp1_ns - t0) / hour_ns if tp1_ns is not None else np.nan,
                mfe_r,
                mae_r,
                gain + swap_r,
                r1 + swap1,
                r2 + swap2,
                swap_r,
            )
        )

    empty_row = (False, pd.NaT, *([np.nan] * 5), None, pd.NaT, *([np.nan] * 8))
    out = pd.DataFrame(
        [rec if rec is not None else empty_row for rec in records], index=cand.index, columns=LABEL_COLUMNS
    )
    out["complete"] = out["complete"].astype(bool)
    return out
