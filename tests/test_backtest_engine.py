from __future__ import annotations

import pandas as pd
import pytest

from backtest.costs import CostModel
from backtest.engine import BacktestEngine
from strategy.base import SIGNAL_COLUMNS

INSTRUMENT = "EURUSD"
ZERO_COST = CostModel(
    default_spread_pips=0, commission_per_lot_round_turn_eur=0,
    slippage_pips=0, default_swap_per_lot_per_night_eur=0,
)


def _ts(hours_offset: int) -> pd.Timestamp:
    return pd.Timestamp("2024-01-02T08:00:00Z") + pd.Timedelta(hours=hours_offset)


def _bar(hours_offset: int, open_, high, low, close) -> dict:
    return {"ts": _ts(hours_offset), "open": open_, "high": high, "low": low, "close": close, "volume": 100.0}


def _signal(hours_offset: int, direction: str, entry: float, stop: float, target: float) -> dict:
    return {
        "ts": _ts(hours_offset), "instrument": INSTRUMENT, "timeframe": "H1", "strategy": "test_strategy",
        "direction": direction, "entry_price": entry, "stop_loss": stop, "take_profit": target,
        "confidence": 0.8, "reason": "escenario de test",
    }


def _rate_1(_ts):
    return 1.0


def test_long_trade_hits_take_profit_with_zero_costs():
    bars = pd.DataFrame([
        _bar(0, 1.0995, 1.1005, 1.0990, 1.1000),
        _bar(1, 1.1000, 1.1050, 1.0980, 1.1010),
        _bar(2, 1.1010, 1.1120, 1.1010, 1.1090),  # dispara el take profit (1.1100) dentro de la barra
    ])
    signals = pd.DataFrame([_signal(0, "long", 1.1000, 1.0950, 1.1100)], columns=SIGNAL_COLUMNS)

    engine = BacktestEngine(ZERO_COST, initial_capital_eur=250, risk_pct=5)
    trades, equity_curve = engine.run(INSTRUMENT, bars, signals, _rate_1)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "target"
    assert trade.entry_price == pytest.approx(1.1000)
    assert trade.exit_price == pytest.approx(1.1100)
    # riesgo 5% de 250 = 12.5 EUR -> lotes redondeados hacia abajo a 0.02 -> riesgo real 10 EUR
    assert trade.lots == pytest.approx(0.02)
    assert trade.risk_eur == pytest.approx(10.0)
    # pnl = (1.1100-1.1000) * 0.02 lotes * 100000 unidades/lote = 20 EUR (rate=1.0, costes=0)
    assert trade.pnl_eur == pytest.approx(20.0)
    assert engine.capital == pytest.approx(270.0)
    assert len(equity_curve) == 1


def test_short_trade_hits_stop_loss_with_zero_costs():
    bars = pd.DataFrame([
        _bar(0, 1.1005, 1.1010, 1.0995, 1.1000),
        _bar(1, 1.1000, 1.1060, 1.0990, 1.1055),  # el precio sube: stop de la venta (1.1050) se dispara
    ])
    signals = pd.DataFrame([_signal(0, "short", 1.1000, 1.1050, 1.0900)], columns=SIGNAL_COLUMNS)

    engine = BacktestEngine(ZERO_COST, initial_capital_eur=250, risk_pct=5)
    trades, _ = engine.run(INSTRUMENT, bars, signals, _rate_1)

    assert len(trades) == 1
    assert trades[0].exit_reason == "stop"
    assert trades[0].exit_price == pytest.approx(1.1050)
    assert trades[0].pnl_eur < 0


def test_ambiguous_bar_assumes_stop_hit_first_by_default():
    bars = pd.DataFrame([
        _bar(0, 1.0995, 1.1005, 1.0990, 1.1000),
        # dentro de esta barra se tocan TANTO el stop (1.0950) como el target (1.1100)
        _bar(1, 1.1000, 1.1150, 1.0900, 1.1050),
    ])
    signals = pd.DataFrame([_signal(0, "long", 1.1000, 1.0950, 1.1100)], columns=SIGNAL_COLUMNS)

    engine = BacktestEngine(ZERO_COST, initial_capital_eur=250, risk_pct=5, stop_wins_ties=True)
    trades, _ = engine.run(INSTRUMENT, bars, signals, _rate_1)

    assert trades[0].exit_reason == "stop"
    assert trades[0].exit_price == pytest.approx(1.0950)


def test_open_position_is_force_closed_at_end_of_backtest():
    bars = pd.DataFrame([
        _bar(0, 1.0995, 1.1005, 1.0990, 1.1000),
        _bar(1, 1.1000, 1.1020, 1.0995, 1.1015),  # nunca toca stop (1.0950) ni target (1.1500)
    ])
    signals = pd.DataFrame([_signal(0, "long", 1.1000, 1.0950, 1.1500)], columns=SIGNAL_COLUMNS)

    engine = BacktestEngine(ZERO_COST, initial_capital_eur=250, risk_pct=5)
    trades, _ = engine.run(INSTRUMENT, bars, signals, _rate_1)

    assert len(trades) == 1
    assert trades[0].exit_reason == "backtest_end"
    assert trades[0].exit_price == pytest.approx(1.1015)  # close de la ultima barra


def test_costs_reduce_realized_pnl_versus_zero_cost_baseline():
    bars = pd.DataFrame([
        _bar(0, 1.0995, 1.1005, 1.0990, 1.1000),
        _bar(1, 1.1000, 1.1050, 1.0980, 1.1010),
        _bar(2, 1.1010, 1.1120, 1.1010, 1.1090),
    ])
    signals = pd.DataFrame([_signal(0, "long", 1.1000, 1.0950, 1.1100)], columns=SIGNAL_COLUMNS)

    costly = CostModel(
        default_spread_pips=2.0, commission_per_lot_round_turn_eur=7.0,
        slippage_pips=0.5, default_swap_per_lot_per_night_eur=-2.5,
    )
    engine_zero = BacktestEngine(ZERO_COST, initial_capital_eur=250, risk_pct=5)
    trades_zero, _ = engine_zero.run(INSTRUMENT, bars, signals, _rate_1)

    engine_costly = BacktestEngine(costly, initial_capital_eur=250, risk_pct=5)
    trades_costly, _ = engine_costly.run(INSTRUMENT, bars, signals, _rate_1)

    assert trades_costly[0].pnl_eur < trades_zero[0].pnl_eur


def test_capital_compounds_sequentially_across_trades():
    bars = pd.DataFrame([
        _bar(0, 1.0995, 1.1005, 1.0990, 1.1000),
        _bar(1, 1.1010, 1.1120, 1.1000, 1.1090),  # trade 1: target
        _bar(2, 1.1090, 1.1095, 1.1085, 1.1090),
        _bar(3, 1.1200, 1.1210, 1.1090, 1.1090),  # trade 2: target, abre con capital ya incrementado
    ])
    signals = pd.DataFrame([
        _signal(0, "long", 1.1000, 1.0950, 1.1100),
        # distancia al stop mas ajustada (0.0045 en vez de 0.0050) para que el
        # redondeo a step de lote no oculte el efecto del capital compuesto
        _signal(2, "long", 1.1090, 1.1045, 1.1190),
    ], columns=SIGNAL_COLUMNS)

    engine = BacktestEngine(ZERO_COST, initial_capital_eur=250, risk_pct=5)
    trades, _ = engine.run(INSTRUMENT, bars, signals, _rate_1)

    assert len(trades) == 2
    # el riesgo en EUR de la segunda operacion debe basarse en el capital YA
    # incrementado por la primera, no en los 250 EUR iniciales.
    assert trades[1].risk_eur > trades[0].risk_eur
