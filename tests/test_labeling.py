"""Casos conocidos del etiquetado (stop / objetivos / tiempo, con costes)."""

from __future__ import annotations

import pandas as pd
import pytest

from finance_bot.config import CostSpec, InstrumentConfig, SwapSpec
from finance_bot.engine.labeling import label_candidates, rollover_schedule
from tests.conftest import make_m1

SIGNAL = pd.Timestamp("2024-03-12 10:00", tz="UTC")


def _cand(direction: int, r: float, when: pd.Timestamp = SIGNAL, entry: float | None = None) -> pd.DataFrame:
    data = {"close_time": [when], "direction": [direction], "sl_distance": [r]}
    if entry is not None:
        data["entry_override"] = [entry]
    return pd.DataFrame(data, index=[when])


def _label(cand, path, inst, max_bars=60, targets=(1.0, 2.0), partial=0.5):
    return label_candidates(cand, path, inst, 1, max_bars, targets, partial, 1).iloc[0]


def test_long_hits_tp1_then_tp2(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 101, 102, 103, 104, 105])
    res = _label(_cand(1, 2.0), path, zero_cost_gold)
    assert res.complete and res.hit_tp1 == 1 and res.hit_tp2 == 1
    assert res.exit_reason == "tp2"
    assert res.realized_r == pytest.approx(0.5 * 1 + 0.5 * 2)  # mitad en 1R, mitad en 2R


def test_long_stop_first(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 99, 98, 97])
    res = _label(_cand(1, 2.0), path, zero_cost_gold)
    assert res.exit_reason == "stop" and res.hit_tp1 == 0
    assert res.realized_r == pytest.approx(-1.0)


def test_same_bar_touching_stop_and_target_counts_as_stop(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 100])
    path.iloc[1, path.columns.get_loc("high")] = 103  # toca TP1 (102)...
    path.iloc[1, path.columns.get_loc("low")] = 97  # ...y el stop (98) en la misma vela
    res = _label(_cand(1, 2.0), path, zero_cost_gold)
    assert res.exit_reason == "stop" and res.hit_tp1 == 0


def test_break_even_after_tp1(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 101, 102, 101, 100, 99, 98])
    res = _label(_cand(1, 2.0), path, zero_cost_gold)
    assert res.exit_reason == "tp1_be" and res.hit_tp1 == 1 and res.hit_tp2 == 0
    assert res.realized_r == pytest.approx(0.5)  # 0.5R de la parte cerrada + 0 del resto


def test_time_exit_marks_to_market(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 100.5, 101, 100.5, 101] * 4)
    res = _label(_cand(1, 5.0), path, zero_cost_gold, max_bars=5)
    assert res.exit_reason == "time" and res.complete
    assert res.hours_to_exit == pytest.approx(5 / 60)


def test_short_pays_spread_on_exit(cfg) -> None:
    inst = cfg.instrument("EURUSD").model_copy(
        update={
            "spread": CostSpec(kind="abs", value=0.0002),
            "slippage": CostSpec(kind="abs", value=0.0),
            "swap_per_night": SwapSpec(long=0.0, short=0.0),
        }
    )
    # BID baja exactamente hasta el TP1 teorico pero el ASK (bid + spread) no llega: NO es TP1
    path = make_m1("2024-03-12 10:00", [1.1000, 1.0995, 1.0990, 1.0992])
    res = _label(_cand(-1, 0.0010), path, inst)
    assert res.hit_tp1 == 0


def test_gap_through_stop_fills_at_open(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 100, 95])
    path.iloc[2, path.columns.get_loc("open")] = 95
    path.iloc[2, path.columns.get_loc("high")] = 95
    res = _label(_cand(1, 2.0), path, zero_cost_gold)
    assert res.exit_reason == "stop"
    assert res.realized_r == pytest.approx(-2.5)  # hueco: sale a 95, no a 98


def test_incomplete_window_is_not_marked_complete(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 100.5, 100.2])  # ni stop ni objetivo, ventana sin terminar
    res = _label(_cand(1, 5.0), path, zero_cost_gold, max_bars=60)
    assert not res.complete


def test_open_trade_after_tp1_stays_open_for_live_tracking(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 101, 102, 103])  # TP1 tocado, TP2 aun no, ventana abierta
    res = _label(_cand(1, 2.0), path, zero_cost_gold, max_bars=60)
    assert res.hit_tp1 == 1 and not res.complete


def test_entry_override_uses_sent_price(zero_cost_gold: InstrumentConfig) -> None:
    path = make_m1("2024-03-12 10:00", [100, 101, 102, 103, 104, 105])
    res = _label(_cand(1, 2.0, entry=101.0), path, zero_cost_gold)
    assert res.entry_price == 101.0  # TP1 = 103, TP2 = 105
    assert res.exit_reason == "tp2"


def test_wednesday_rollover_counts_triple() -> None:
    start, end = pd.Timestamp("2024-03-11", tz="UTC"), pd.Timestamp("2024-03-16", tz="UTC")
    stamps, cumw = rollover_schedule(start, end)
    assert cumw[-1] - cumw[0] >= 7  # lun-vie con miercoles x3 = 1+1+3+1+1


def test_costs_make_random_trades_lose_on_average(cfg) -> None:
    """Con costes, entradas en un camino sin tendencia deben perder de media."""
    inst = cfg.instrument("XAUUSD")
    path = make_m1("2024-03-12 00:00", [2000 + (i % 7) * 0.3 for i in range(3000)])
    times = path.index[::97][:25]
    cand = pd.DataFrame({"close_time": times, "direction": [1, -1] * 12 + [1], "sl_distance": 3.0}, index=times)
    res = label_candidates(cand, path, inst, 1, 200, (1.0, 2.0), 0.5, 1)
    assert res.loc[res.complete, "realized_r"].mean() < 0
