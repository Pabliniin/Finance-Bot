"""Avisos de gestion de una señal abierta: solo saltan cuando hay motivo, y el
R que guardan es el de cerrar AHORA (por el lado malo del spread)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from finance_bot.data.calendar import EconomicEvent
from finance_bot.engine import exits
from finance_bot.engine.signals import TimeframeView, VoteView

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _signal_row(**overrides) -> pd.Series:
    base = {
        "key": "k",
        "symbol": "XAUUSD",
        "tf": "H1",
        "direction": 1,
        "entry": 100.0,
        "r_price": 2.0,
        "max_hours": 48.0,
        "signal_time": pd.Timestamp("2026-09-22 06:00", tz="UTC"),
    }
    base.update(overrides)
    return pd.Series(base)


def _view(score: int, triggers_long: list[str] | None = None, triggers_short: list[str] | None = None) -> TimeframeView:
    votes = [VoteView(f"v{i}", f"voto {i}", "trend", 1 if i < abs(score) else 0, "") for i in range(20)]
    if score < 0:
        votes = [VoteView(v.key, v.name, v.family, -v.vote, v.detail) for v in votes]
    return TimeframeView(
        tf="H1",
        bar_close=pd.Timestamp("2026-09-22 11:00", tz="UTC"),
        close=100.0,
        atr=1.0,
        rsi=50.0,
        adx=20.0,
        support=None,
        resistance=None,
        vol_rank=None,
        votes=votes,
        triggers_long=triggers_long or [],
        triggers_short=triggers_short or [],
    )


def test_quiet_when_nothing_happens(cfg) -> None:
    assert exits.evaluate(_signal_row(), _view(4), [], (101.0, 101.2), cfg, NOW) == []


def test_context_flip_against_the_trade_is_urgent(cfg) -> None:
    advice = exits.evaluate(_signal_row(), _view(-8), [], (101.0, 101.2), cfg, NOW)
    assert advice and advice[0].kind == "reversion" and advice[0].urgency == "alta"


def test_context_flip_in_favour_says_nothing(cfg) -> None:
    # una venta con el contexto girado a bajista esta a favor: no hay aviso
    assert exits.evaluate(_signal_row(direction=-1), _view(-8), [], (99.0, 99.2), cfg, NOW) == []


def test_opposite_trigger_fires_an_advice(cfg) -> None:
    advice = exits.evaluate(_signal_row(), _view(0, triggers_short=["Cruce de MACD"]), [], None, cfg, NOW)
    assert [a.kind for a in advice] == ["trigger_opuesto"]


def test_high_impact_news_within_the_blackout(cfg) -> None:
    event = EconomicEvent(NOW + timedelta(hours=1), "USD", "Tipos de interes", "High", "", "")
    advice = exits.evaluate(_signal_row(), _view(0), [event], None, cfg, NOW)
    assert [a.kind for a in advice] == ["noticia"]


def test_news_far_away_is_not_an_advice(cfg) -> None:
    event = EconomicEvent(NOW + timedelta(days=2), "USD", "Tipos de interes", "High", "", "")
    assert exits.evaluate(_signal_row(), _view(0), [event], None, cfg, NOW) == []


def test_time_running_out(cfg) -> None:
    row = _signal_row(signal_time=pd.Timestamp("2026-09-20 20:00", tz="UTC"))  # quedan 8 h de 48
    assert [a.kind for a in exits.evaluate(row, _view(0), [], None, cfg, NOW)] == ["tiempo"]


def test_giving_back_profit(cfg) -> None:
    # llego a +1.5R y ahora va +0.5R (precio 101 con 1R = 2.0)
    advice = exits.evaluate(_signal_row(), _view(0), [], (101.0, 101.2), cfg, NOW, peak_r=1.5)
    assert [a.kind for a in advice] == ["beneficio"]
    assert advice[0].r_now == 0.5


def test_small_giveback_is_not_worth_a_message(cfg) -> None:
    assert exits.evaluate(_signal_row(), _view(0), [], (101.0, 101.2), cfg, NOW, peak_r=0.9) == []


def test_r_now_uses_the_bad_side_of_the_spread() -> None:
    # compra: se cierra en el bid (100.5); venta: en el ask (101.0)
    assert exits.r_now(100.0, 1.0, 1, (100.5, 101.0)) == 0.5
    assert exits.r_now(100.0, 1.0, -1, (100.5, 101.0)) == -1.0
    assert exits.r_now(100.0, 1.0, 1, None) is None


def test_urgent_advice_comes_first(cfg) -> None:
    event = EconomicEvent(NOW + timedelta(hours=1), "USD", "Tipos", "High", "", "")
    advice = exits.evaluate(_signal_row(), _view(-8), [event], (101.0, 101.2), cfg, NOW)
    assert [a.urgency for a in advice] == sorted([a.urgency for a in advice], key=lambda u: u != "alta")
    assert advice[0].urgency == "alta"


def test_the_signal_bar_itself_never_counts_as_a_reversal(cfg) -> None:
    """La vela que genero la señal es la ultima cerrada en ese mismo escaneo: no
    puede a la vez 'girarse en contra'. Solo velas posteriores."""
    view = _view(-8)  # cierra 11:00
    same_bar = _signal_row(signal_time=pd.Timestamp("2026-09-22 11:00", tz="UTC"))
    assert exits.evaluate(same_bar, view, [], (101.0, 101.2), cfg, NOW) == []
    earlier = _signal_row(signal_time=pd.Timestamp("2026-09-22 10:00", tz="UTC"))
    assert [a.kind for a in exits.evaluate(earlier, view, [], (101.0, 101.2), cfg, NOW)] == ["reversion"]
