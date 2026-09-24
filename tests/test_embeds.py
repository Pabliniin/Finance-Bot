"""Los mensajes de Discord: nada de texto ajeno sin escapar, ningun limite de
la API superado y las cifras clave siempre visibles."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from finance_bot.data.calendar import EconomicEvent
from finance_bot.discord_bot import embeds
from finance_bot.engine.exits import ExitAdvice
from finance_bot.engine.signals import LadderStep
from tests.test_signals import _signal


def _ladder() -> list[LadderStep]:
    return [LadderStep(r, 0.5, 0.4, 0.6) for r in (0.5, 1.0, 1.5, 2.0, 3.0)]


def test_news_title_cannot_inject_formatting_or_mentions(cfg) -> None:
    evil = EconomicEvent(
        datetime(2026, 9, 23, 12, 30, tzinfo=UTC), "USD", "**PUMP** @everyone https://x.test", "High", "", ""
    )
    text = embeds.to_text(embeds.signal_embed(_signal(news=[evil]), cfg))
    assert "@everyone" not in text  # queda neutralizado con un caracter invisible
    assert "**PUMP**" not in text and "PUMP" in text


def test_signal_shows_model_probability_and_what_actually_happened(cfg) -> None:
    embed = embeds.signal_embed(_signal(p_tp1=0.55, similar_tp1_rate=0.45, similar_n=792, ladder=_ladder()), cfg)
    field = next(f for f in embed.fields if "Probabilidad" in f.name)
    assert "55%" in field.value and "45%" in field.value and "792" in field.value


def test_unvalidated_signal_is_clearly_marked(cfg) -> None:
    embed = embeds.signal_embed(_signal(validated=False), cfg)
    assert "SIN VENTAJA VALIDADA" in (embed.description or "")


def test_signal_shows_live_spread_condition(cfg) -> None:
    """Con cotizacion en vivo (MT5), la señal muestra si el spread esta normal o
    ancho ahora mismo; sin cotizacion, no aparece el campo."""
    wide = embeds.signal_embed(_signal(live_quote=True, spread_ratio=2.5), cfg)
    assert any("Spread" in f.name and "ANCHO" in f.value for f in wide.fields)
    normal = embeds.signal_embed(_signal(live_quote=True, spread_ratio=1.0), cfg)
    assert any("Spread" in f.name and "normal" in f.value for f in normal.fields)
    estimated = embeds.signal_embed(_signal(live_quote=False, spread_ratio=None), cfg)
    assert not any("Spread" in f.name for f in estimated.fields)


def test_signal_respects_discord_limits(cfg) -> None:
    long_note = "x" * 3000
    signal = _signal(
        similar_scope=long_note,
        ladder=_ladder(),
        warnings=[long_note, long_note],
        triggers=[long_note],
    )
    embed = embeds.signal_embed(signal, cfg)
    assert len(embed) <= 6000
    assert len(embed.fields) <= embeds.MAX_FIELDS
    assert all(len(f.value) <= embeds.FIELD_LIMIT for f in embed.fields)
    assert all(len(f.name) <= 256 for f in embed.fields)


def test_every_signal_carries_the_disclaimer(cfg) -> None:
    embed = embeds.signal_embed(_signal(), cfg)
    footer = (embed.footer.text or "").lower()
    assert "no es asesoramiento" in footer and "garantia" in footer


def test_exit_advice_says_it_is_not_validated(cfg) -> None:
    advice = ExitAdvice(
        key="k",
        symbol="XAUUSD",
        tf="H1",
        direction=1,
        kind="reversion",
        urgency="alta",
        headline="El contexto se ha girado en contra",
        detail="12/20 votos en contra",
        r_now=0.4,
    )
    text = embeds.to_text(embeds.advice_embed(advice, cfg))
    assert "+0.40R" in text
    assert "no" in text.lower() and "validado" in text.lower()


def test_closed_event_compares_advice_with_the_plan(cfg) -> None:
    event = {
        "type": "closed",
        "symbol": "XAUUSD",
        "tf": "H1",
        "direction": 1,
        "reason": "stop",
        "realized_r": -1.0,
        "hours": 6.0,
        "advice_r": 0.5,
        "advice_headline": "El contexto se ha girado en contra",
    }
    text = embeds.to_text(embeds.event_embed(event, cfg))
    assert "-1.00R" in text and "+0.50R" in text and "habria sido mejor" in text


def test_stats_warn_about_small_samples(cfg) -> None:
    closed = pd.DataFrame(
        {
            "realized_r": [1.0, -1.0, 0.5],
            "hit_tp1": [1.0, 0.0, 1.0],
            "hit_tp2": [1.0, 0.0, 0.0],
            "p_tp1": [0.6, 0.6, 0.6],
            "symbol": "XAUUSD",
            "tf": "H1",
        }
    )
    text = embeds.to_text(embeds.stats_embed(closed, pd.DataFrame(), "30d", cfg))
    assert "ruido" in text


def test_stats_reports_model_reliability_with_enough_signals(cfg) -> None:
    """Con muestra suficiente, /stats dice si los aciertos van en linea con lo
    prometido o por debajo: la respuesta honesta a '¿son efectivas?'."""
    n = 24
    closed = pd.DataFrame(
        {
            "realized_r": [0.5, -1.0] * (n // 2),
            "hit_tp1": [1.0, 0.0] * (n // 2),  # 50% de aciertos reales
            "hit_tp2": 0.0,
            "p_tp1": [0.9] * n,  # el modelo prometia 90%: muy por debajo
            "symbol": "XAUUSD",
            "tf": "H1",
        }
    )
    text = embeds.to_text(embeds.stats_embed(closed, pd.DataFrame(), "todo", cfg))
    assert "Fiabilidad" in text and "DEBAJO" in text


def test_panel_shows_open_signals_and_kill_switch(cfg) -> None:
    health = {
        "kill_switch": (True, "drawdown real de 13R"),
        "mode": "strict",
        "feed": "MT5",
        "last_scan": datetime(2026, 9, 22, 10, tzinfo=UTC),
    }
    open_signals = pd.DataFrame(
        {
            "key": ["k1"],
            "symbol": ["XAUUSD"],
            "tf": ["H1"],
            "direction": [1],
            "entry": [4300.0],
            "tp1_notified": [False],
        }
    )
    text = embeds.to_text(embeds.panel_embed({}, open_signals, health, cfg, {"k1": 0.75}))
    assert "+0.75R" in text and "drawdown real de 13R" in text


def test_naive_timestamps_are_treated_as_utc() -> None:
    assert embeds.when(pd.Timestamp("2026-09-22 10:00")) == embeds.when(pd.Timestamp("2026-09-22 10:00", tz="UTC"))


def test_signal_shows_entry_zone_and_every_target(cfg) -> None:
    signal = _signal(
        entry=4341.0,
        stop=4320.5,
        r_price=20.5,
        entry_low=4336.9,
        entry_high=4341.0,
        extra_target_r=3.0,
        ladder=_ladder(),
    )
    text = embeds.to_text(embeds.signal_embed(signal, cfg))
    assert "4,336.90 – 4,341.00" in text  # rango, no un unico precio
    assert "TP1" in text and "TP2" in text and "TP3" in text and "SL" in text
    assert "4,402.50" in text  # TP3 = entrada + 3R
    assert "TP3 es extra" in text  # el objetivo de mas queda marcado como fuera del plan
    assert "entrada validada" not in text  # no contradice la cabecera "SIN VENTAJA VALIDADA"


def test_signal_without_zone_falls_back_to_one_price(cfg) -> None:
    text = embeds.to_text(embeds.signal_embed(_signal(entry=100.0, entry_low=None, entry_high=None), cfg))
    assert "Zona  100.00" in text and "–" not in text


def test_signal_message_stays_short(cfg) -> None:
    """Tiene que leerse de un vistazo en el movil, no ser un ladrillo."""
    embed = embeds.signal_embed(_signal(ladder=_ladder()), cfg)
    assert len(embed.fields) <= 6
    assert len(embeds.to_text(embed)) < 1200
