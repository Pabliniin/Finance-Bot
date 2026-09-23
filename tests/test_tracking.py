"""Seguimiento de extremo a extremo con un almacen temporal: señal registrada
-> TP1 notificado una sola vez -> cierre con resultado; y kill switch."""

from __future__ import annotations

import pandas as pd
import pytest

from finance_bot.data.market import MarketData
from finance_bot.tracking import Tracker
from tests.conftest import make_m1
from tests.test_signals import _signal


@pytest.fixture
def env(cfg, tmp_path):
    local_cfg = cfg.model_copy(update={"data": cfg.data.model_copy(update={"store_dir": str(tmp_path)})})
    return local_cfg, MarketData(local_cfg), Tracker(local_cfg, db_path=tmp_path / "bot.sqlite3")


def test_signal_lifecycle_tp1_then_close(env) -> None:
    cfg, md, tracker = env
    signal = _signal(entry=2000.0, stop=1990.0, tp1=2010.0, tp2=2020.0, r_price=10.0)
    tracker.record(signal)
    assert tracker.is_known(signal.key)

    # precio sube hasta TP1 (2010) sin tocar TP2 ni volver a la entrada
    md.m1_store.write("XAUUSD", make_m1("2026-09-22 10:00", [2000, 2004, 2008, 2011, 2012]))
    events = tracker.update_open(md)
    assert [e["type"] for e in events] == ["tp1"]
    assert tracker.update_open(md) == []  # no se notifica dos veces

    # sigue hasta TP2 (2020) -> cerrada con 0.5*1R + 0.5*2R menos costes
    md.m1_store.write("XAUUSD", make_m1("2026-09-22 10:05", [2014, 2017, 2021]))
    events = tracker.update_open(md)
    assert events and events[0]["type"] == "closed" and events[0]["reason"] == "tp2"
    closed = tracker.closed_signals()
    assert len(closed) == 1 and closed["realized_r"].iloc[0] == pytest.approx(1.5, abs=0.05)
    assert tracker.open_signals().empty


def test_kill_switch_trips_on_drawdown_and_needs_manual_reset(env) -> None:
    cfg, md, tracker = env
    for i in range(25):
        s = _signal(key=f"k{i}", signal_time=pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i))
        tracker.record(s)
    with tracker.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE signals SET status='closed', realized_r=-1.0, hit_tp1=0, hit_tp2=0, exit_time=signal_time"
        )
    reason = tracker.evaluate_kill_switch()
    assert reason is not None and "drawdown" in reason
    active, _ = tracker.kill_switch_status()
    assert active
    assert tracker.evaluate_kill_switch() is None  # sigue activo, no se re-evalua ni se levanta solo
    tracker.reset_kill_switch()
    assert tracker.kill_switch_status() == (False, None)


def test_user_settings_persist(env) -> None:
    cfg, md, tracker = env
    assert tracker.capital_eur() == cfg.account.capital_eur
    tracker.set_setting("capital_eur", "500")
    tracker.set_setting("mode", "informative")
    assert tracker.capital_eur() == 500.0 and tracker.mode() == "informative"


def test_manual_trade_is_followed_but_kept_out_of_the_model_stats(env) -> None:
    _, _, tracker = env
    key = tracker.follow_manual("XAUUSD", "H1", 1, 4350.0, 4330.0)
    opened = tracker.open_signals()
    row = opened[opened["key"] == key].iloc[0]
    assert row["source"] == "manual" and row["validated"] == 0
    assert row["tp1"] == 4370.0 and row["tp2"] == 4390.0  # 1R = 20, plan equilibrado
    assert tracker.closed_signals().empty  # aun abierta
    assert tracker.stop_following(key)
    assert tracker.closed_signals().empty  # cerrada, pero fuera de las cifras del modelo
    assert len(tracker.closed_signals(source="manual")) == 1


def test_manual_trade_rejects_a_stop_on_the_wrong_side(env) -> None:
    _, _, tracker = env

    with pytest.raises(ValueError, match="stop"):
        tracker.follow_manual("XAUUSD", "H1", 1, 4350.0, 4360.0)
    with pytest.raises(ValueError, match="stop"):
        tracker.follow_manual("XAUUSD", "H1", -1, 4350.0, 4340.0)


def test_open_signals_can_be_filtered_by_source(env) -> None:
    _, _, tracker = env
    tracker.record(_signal())
    tracker.follow_manual("EURUSD", "H1", -1, 1.1, 1.11)
    assert len(tracker.open_signals()) == 2
    assert len(tracker.open_signals(source="bot")) == 1
    assert len(tracker.open_signals(source="manual")) == 1


def test_kill_switch_ignores_unvalidated_signals(env) -> None:
    """Un setup marcado 'sin ventaja' no promete nada: sus resultados no pueden
    disparar el interruptor que compara lo real con lo prometido."""
    import pandas as pd
    from sqlalchemy import update

    from finance_bot.tracking import signals_table

    _, _, tracker = env
    for i in range(25):
        tracker.record(_signal(key=f"k{i}", validated=False, signal_time=pd.Timestamp("2026-09-01", tz="UTC")))
    with tracker.engine.begin() as conn:
        conn.execute(
            update(signals_table).values(
                status="closed", realized_r=-1.0, hit_tp1=0.0, hit_tp2=0.0, exit_time=pd.Timestamp("2026-09-02")
            )
        )
    assert tracker.evaluate_kill_switch() is None  # 25 perdidas seguidas, pero ninguna validada
