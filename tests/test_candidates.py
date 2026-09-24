"""Reglas de generacion de candidatos: un candidato nace donde dispara un
trigger (y solo en un sentido), respeta las horas permitidas y coloca el stop
tras la estructura, acotado en ATR."""

from __future__ import annotations

import pandas as pd

from finance_bot.config import load_config
from finance_bot.engine.candidates import generate_candidates
from finance_bot.strategies.triggers import TRIGGER_KEYS

PLAN = load_config().timeframes["H1"]


def _frame(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02 08:00", periods=n, freq="1h", tz="UTC").as_unit("ns")
    df = pd.DataFrame(index=pd.DatetimeIndex(idx, name="time"))
    df["close_time"] = idx + pd.Timedelta(hours=1)
    df["open"] = df["close"] = 100.0
    df["high"] = 101.0
    df["low"] = 99.0
    for col in ("ema200", "adx", "rsi", "macd", "supertrend_dir"):
        df[col] = 1.0
    df["atr"] = 1.0
    for k in TRIGGER_KEYS:
        df[f"trig_{k}"] = 0.0
    return df


def test_a_single_direction_trigger_creates_one_candidate() -> None:
    df = _frame()
    df.loc[df.index[-1], "trig_pullback_ema"] = 1.0  # dispara en largo en la ultima vela
    cand = generate_candidates(df, "XAUUSD", "H1", PLAN)
    assert len(cand) == 1
    assert cand.index[-1] == df.index[-1]
    assert int(cand.iloc[0]["direction"]) == 1
    assert cand.iloc[0]["triggers"] == ["pullback_ema"]


def test_conflicting_triggers_produce_no_candidate() -> None:
    df = _frame()
    df.loc[df.index[-1], "trig_pullback_ema"] = 1.0  # largo
    df.loc[df.index[-1], "trig_macd_cross"] = -1.0  # y corto a la vez -> ruido, sin candidato
    assert generate_candidates(df, "XAUUSD", "H1", PLAN).empty


def test_allowed_hours_filter_candidates_outside_the_session() -> None:
    df = _frame()
    df.loc[df.index[-1], "trig_pullback_ema"] = 1.0
    hour = int(df["close_time"].iloc[-1].hour)
    assert generate_candidates(df, "XAUUSD", "H1", PLAN, allowed_hours=[hour]).shape[0] == 1
    other = [h for h in range(24) if h != hour]
    assert generate_candidates(df, "XAUUSD", "H1", PLAN, allowed_hours=other).empty


def test_stop_distance_is_clamped_between_atr_bounds() -> None:
    df = _frame()
    df.loc[df.index[-1], "trig_pullback_ema"] = 1.0
    cand = generate_candidates(df, "XAUUSD", "H1", PLAN)
    sl = float(cand.iloc[0]["sl_distance"])
    atr = float(cand.iloc[0]["atr"])
    assert PLAN.sl_atr_min * atr <= sl <= PLAN.sl_atr_max * atr


def test_missing_required_indicator_blocks_the_candidate() -> None:
    df = _frame()
    df.loc[df.index[-1], "trig_pullback_ema"] = 1.0
    df.loc[df.index[-1], "ema200"] = float("nan")  # sin EMA200 no hay contexto -> no candidato
    assert generate_candidates(df, "XAUUSD", "H1", PLAN).empty
