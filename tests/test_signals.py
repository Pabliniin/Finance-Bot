from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from finance_bot.data.calendar import EconomicEvent
from finance_bot.engine.signals import Signal, SignalEngine, VoteView, probability_ladder, similar_cases


def _history(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "symbol": rng.choice(["XAUUSD", "EURUSD"], size=n),
            "tf": rng.choice(["H1", "H4"], size=n),
            "direction": rng.choice([-1, 1], size=n),
            "p_tp1": rng.uniform(0.3, 0.6, size=n),
            "mfe_r": rng.exponential(1.0, size=n),
            "realized_r": rng.normal(0, 1, size=n),
            "hit_tp1": rng.integers(0, 2, size=n),
        }
    )


def test_similar_cases_prefers_most_specific_tier() -> None:
    h = _history()
    cases, scope = similar_cases(h, "XAUUSD", "H1", 1, 0.45, min_n=10)
    assert "misma direccion" in scope
    assert (cases["symbol"] == "XAUUSD").all() and (cases["direction"] == 1).all()
    assert ((cases["p_tp1"] - 0.45).abs() <= 0.05).all()


def test_similar_cases_widens_when_too_few() -> None:
    h = _history()
    _, scope = similar_cases(h, "XAUUSD", "H1", 1, 0.45, min_n=1000)
    assert "todas" in scope


def test_ladder_is_monotonic_decreasing() -> None:
    steps = probability_ladder(_history())
    probs = [s.probability for s in steps]
    assert probs == sorted(probs, reverse=True)
    assert all(s.ci_low <= s.probability <= s.ci_high for s in steps)


def _signal(**overrides) -> Signal:
    base = dict(
        key="k",
        symbol="XAUUSD",
        tf="H1",
        direction=1,
        plan="equilibrado",
        validated=True,
        signal_time=pd.Timestamp("2026-09-22 10:00", tz="UTC"),
        entry=100.0,
        stop=98.0,
        tp1=102.0,
        tp2=104.0,
        r_price=2.0,
        targets_r=(1.0, 2.0),
        partial=0.5,
        p_tp1=0.60,
        p_tp2=0.35,
        threshold=0.55,
        ladder=[],
        similar_n=200,
        similar_scope="x",
        similar_ev=0.15,
        similar_tp1_rate=0.58,
        hours_median=8.0,
        hours_p75=20.0,
        hours_to_tp1_median=4.0,
        max_hours=48.0,
        triggers=["t"],
        # confluencia fuerte por defecto (12/20 a favor): la señal tipica de test emite
        votes_for=[VoteView(f"v{i}", f"voto {i}", "trend", 1, "") for i in range(12)],
        votes_against=[],
        top_factors=[],
        news=[],
        live_quote=True,
        cost_r=0.05,
        size=None,
    )
    base.update(overrides)
    return Signal(**base)


def test_gates_strict_mode(cfg) -> None:
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    now = datetime(2026, 9, 22, 10, tzinfo=UTC)

    good = _signal()
    engine._apply_gates(good, "strict", now)
    assert good.emit

    not_validated = _signal(validated=False)
    engine._apply_gates(not_validated, "strict", now)
    assert not not_validated.emit

    low_prob = _signal(p_tp1=0.50)
    engine._apply_gates(low_prob, "strict", now)
    assert any("umbral" in b for b in low_prob.blockers)

    bad_ev = _signal(similar_ev=-0.10)
    engine._apply_gates(bad_ev, "strict", now)
    assert not bad_ev.emit


def test_informative_mode_turns_validation_and_ev_into_warnings(cfg) -> None:
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    s = _signal(validated=False, similar_ev=-0.10)
    engine._apply_gates(s, "informative", datetime(2026, 9, 22, 10, tzinfo=UTC))
    assert s.emit
    assert any("SIN VENTAJA VALIDADA" in w for w in s.warnings)


def test_late_signal_never_emits_only_fresh_ones(cfg) -> None:
    """Solo señales frescas (recien disparadas). Una vela vieja no se emite en
    ningun modo: evita el aluvion de setups viejos al arrancar."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    late_now = datetime(2026, 9, 22, 11, 30, tzinfo=UTC)  # H1 cerro a las 10:00, 90 min tarde
    for mode in ("strict", "informative"):
        s = _signal(tf="H1", validated=False, similar_ev=0.10)
        engine._apply_gates(s, mode, late_now)
        assert not s.emit and any("la vela cerro" in b for b in s.blockers)


def test_weak_confluence_is_filtered_out(cfg) -> None:
    """Pocas estrategias a favor -> no se emite, ni en informativo: el usuario
    quiere señales solidas, no cualquier setup."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    now = datetime(2026, 9, 22, 10, 5, tzinfo=UTC)
    weak = _signal(  # 4 a favor, 3 en contra: confluencia neta 1 < 8
        votes_for=[VoteView(f"v{i}", "x", "trend", 1, "") for i in range(4)],
        votes_against=[VoteView(f"w{i}", "x", "trend", -1, "") for i in range(3)],
    )
    engine._apply_gates(weak, "informative", now)
    assert not weak.emit and any("confluencia debil" in b for b in weak.blockers)


def test_news_blackout_blocks_intraday(cfg) -> None:
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    now = datetime(2026, 9, 22, 10, tzinfo=UTC)
    event = EconomicEvent(now + timedelta(hours=1), "USD", "CPI m/m", "High", "", "")
    s = _signal(news=[event])
    engine._apply_gates(s, "strict", now)
    assert any("noticia" in b for b in s.blockers)


def test_targets_list_plan_first_then_the_extra_one() -> None:
    from finance_bot.engine.signals import LadderStep

    ladder = [LadderStep(r, p, p - 0.02, p + 0.02) for r, p in ((1.0, 0.45), (2.0, 0.25), (3.0, 0.14))]
    signal = _signal(entry=100.0, r_price=2.0, targets_r=(1.0, 2.0), ladder=ladder, extra_target_r=3.0)
    rows = signal.targets()
    assert [(label, round(price, 2), r) for label, price, r, _ in rows] == [
        ("TP1", 102.0, 1.0),
        ("TP2", 104.0, 2.0),
        ("TP3", 106.0, 3.0),
    ]
    assert [probability for *_, probability in rows] == [0.45, 0.25, 0.14]


def test_extra_target_is_hidden_when_it_is_not_further_than_tp2() -> None:
    signal = _signal(targets_r=(1.5, 3.0), extra_target_r=3.0)
    assert [label for label, *_ in signal.targets()] == ["TP1", "TP2"]


def test_sell_targets_go_down() -> None:
    signal = _signal(direction=-1, entry=100.0, r_price=2.0, targets_r=(1.0, 2.0), extra_target_r=3.0)
    assert [round(price, 2) for _, price, _, _ in signal.targets()] == [98.0, 96.0, 94.0]


def test_informative_mode_shows_setups_below_threshold_with_a_warning(cfg) -> None:
    """El proposito del modo informativo es enseñar lo que ve: el umbral y los
    casos escasos pasan a avisos. Lo que sigue bloqueando es lo que hace que
    la operacion no sea ejecutable tal como se describe (llega tarde)."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    now = datetime(2026, 9, 22, 10, 5, tzinfo=UTC)

    low = _signal(validated=False, p_tp1=0.48, threshold=0.65, similar_n=5, similar_ev=-0.09)
    engine._apply_gates(low, "informative", now)
    assert low.emit
    assert any("umbral" in w for w in low.warnings)
    assert any("casos similares" in w for w in low.warnings)
    assert any("SIN VENTAJA VALIDADA" in w for w in low.warnings)

    # lo unico que sigue bloqueando en informativo es lo peligroso: una noticia de alto impacto encima
    event = EconomicEvent(now + timedelta(hours=1), "USD", "CPI", "High", "", "")
    blocked = _signal(validated=False, news=[event])
    engine._apply_gates(blocked, "informative", now)
    assert not blocked.emit and any("noticia" in b for b in blocked.blockers)

    strict_low = _signal(p_tp1=0.48, threshold=0.65)
    engine._apply_gates(strict_low, "strict", now)
    assert not strict_low.emit  # en estricto el umbral sigue bloqueando


def test_rank_prefers_expected_value_then_probability() -> None:
    """Entre señales que emiten a la vez se envia la de mayor expectativa; a
    igualdad de expectativa, la de mayor probabilidad de TP1."""
    high_ev = _signal(similar_ev=0.20, p_tp1=0.55)
    high_prob = _signal(similar_ev=0.05, p_tp1=0.80)
    assert high_ev.rank_score > high_prob.rank_score  # gana la de mayor expectativa
    lower_prob = _signal(similar_ev=0.10, p_tp1=0.60)
    higher_prob = _signal(similar_ev=0.10, p_tp1=0.66)
    assert higher_prob.rank_score > lower_prob.rank_score  # desempate por probabilidad


def test_rank_shrinks_expected_value_of_small_samples() -> None:
    """Una expectativa alta con pocos casos pesa menos que una algo menor con
    muchos: la M1 con muestra escasa no gana automaticamente a una H1 solida."""
    solid = _signal(similar_ev=0.15, similar_n=500)
    thin = _signal(similar_ev=0.30, similar_n=20)
    assert solid.rank_score > thin.rank_score


def test_extreme_cost_blocks_in_both_modes(cfg) -> None:
    """Si el coste (spread+deslizamiento) supera tu riesgo, la operacion no tiene
    sentido: no se emite en ningun modo."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    now = datetime(2026, 9, 22, 10, 5, tzinfo=UTC)
    for mode in ("strict", "informative"):
        s = _signal(cost_r=1.5)
        engine._apply_gates(s, mode, now)
        assert not s.emit and any("coste" in b for b in s.blockers)


def test_high_cost_is_a_warning_not_a_block(cfg) -> None:
    """Un coste alto pero asumible (tipico en M1) avisa, no bloquea: el usuario decide."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    s = _signal(cost_r=0.5)  # >= HIGH_COST_R (0.35) pero <= max_cost_r (1.0)
    engine._apply_gates(s, "informative", datetime(2026, 9, 22, 10, 5, tzinfo=UTC))
    assert s.emit and any("coste alto" in w for w in s.warnings)


def test_blown_out_spread_blocks_in_both_modes(cfg) -> None:
    """Spread actual muy por encima del habitual (noticia/iliquidez): no se emite."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    now = datetime(2026, 9, 22, 10, 5, tzinfo=UTC)
    for mode in ("strict", "informative"):
        s = _signal(spread_ratio=5.0)
        engine._apply_gates(s, mode, now)
        assert not s.emit and any("spread ahora" in b for b in s.blockers)


def test_level_before_tp1_is_warned(cfg) -> None:
    """Un nivel en contra antes del TP1 no bloquea, pero se avisa."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    s = _signal(room_to_level_r=0.5)  # nivel a 0.5R, con TP1 a 1.0R
    engine._apply_gates(s, "informative", datetime(2026, 9, 22, 10, 5, tzinfo=UTC))
    assert s.emit and any("antes del TP1" in w for w in s.warnings)


def test_model_vs_similar_divergence_is_warned(cfg) -> None:
    """Si el modelo y la tasa real de casos parecidos discrepan mucho, se avisa
    (podria estar extrapolando); un margen pequeño no molesta."""
    engine = SignalEngine(cfg, md=None, artifacts=None)  # type: ignore[arg-type]
    now = datetime(2026, 9, 22, 10, 5, tzinfo=UTC)
    diverge = _signal(p_tp1=0.70, similar_tp1_rate=0.50)  # 20 pp de diferencia
    engine._apply_gates(diverge, "informative", now)
    assert diverge.emit and any("casos parecidos se cumplio" in w for w in diverge.warnings)
    close = _signal(p_tp1=0.60, similar_tp1_rate=0.58)  # 2 pp: sin aviso
    engine._apply_gates(close, "informative", now)
    assert not any("casos parecidos se cumplio" in w for w in close.warnings)


def test_m1_signal_uses_m15_history_as_labeled_proxy() -> None:
    """M1 no tiene historico propio: similar_cases usa M15 y lo marca."""
    h = _history()
    h["tf"] = "M15"  # solo hay M15 en el historial
    cases, scope = similar_cases(h, "XAUUSD", "M1", 1, 0.45, min_n=5)
    assert "proxy M15" in scope and "sin historico M1" in scope
    assert (cases["tf"] == "M15").all()


def test_m1_is_not_in_the_model_tf_onehots() -> None:
    """El modelo se entreno sin M1: una señal M1 no debe introducir un feature
    tf_M1 (romperia el modelo); cae como 'ninguna TF conocida'."""
    from finance_bot.engine.candidates import model_features
    from finance_bot.strategies.triggers import TRIGGER_KEYS
    from finance_bot.strategies.voters import VOTER_KEYS

    row = {
        "tf": "M1",
        "symbol": "XAUUSD",
        "direction": 1,
        "close_time": pd.Timestamp("2026-09-23 10:00", tz="UTC"),
        "atr": 1.0,
        "adx": 20.0,
        "atr_pct_rank": 0.5,
        "rsi": 55.0,
        "ema200": 99.0,
        "close": 100.0,
        "ema200_slope_atr": 0.1,
        "roc10_atr": 0.1,
        "bb_upper": 101.0,
        "bb_mid": 100.0,
        "resistance": 102.0,
        "support": 98.0,
        "sl_distance": 1.0,
        "htf_rsi": 50.0,
    }
    row.update({f"vote_{k}": 0 for k in VOTER_KEYS})
    row.update({f"trig_{k}": 0 for k in TRIGGER_KEYS})
    feats = model_features(pd.DataFrame([row]))
    assert "tf_M1" not in feats.columns
    assert feats[["tf_M15", "tf_H1", "tf_H4", "tf_D1"]].iloc[0].sum() == 0  # ninguna TF conocida
