"""Detalles de la capa de Discord que se pueden probar sin arrancar el cliente."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from finance_bot.data.calendar import EconomicEvent
from finance_bot.discord_bot import app as app_module


def test_pick_news_alerts_skips_already_alerted() -> None:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    a = EconomicEvent(now, "USD", "NFP", "High", "", "")
    b = EconomicEvent(now, "EUR", "IPC", "High", "", "")
    fresh = app_module.pick_news_alerts([a, b], alerted_keys={a.key})
    assert fresh == [b]  # 'a' ya avisado -> solo queda 'b'
    assert app_module.pick_news_alerts([a, b], {a.key, b.key}) == []


def test_presence_says_market_closed(monkeypatch) -> None:
    """Con el mercado cerrado no se muestran precios viejos como si fueran de
    ahora: el estado del bot dice claramente que esta cerrado."""
    monkeypatch.setattr(app_module, "forex_market_open", lambda now: False)
    # el metodo no usa self en la rama de mercado cerrado: basta un stub
    texts = app_module.FinanceBot._presence_texts(object(), None, {})
    assert texts == ["🌙 mercado cerrado"]

    with_open = app_module.FinanceBot._presence_texts(object(), pd.DataFrame({"key": ["k1", "k2"]}), {})
    assert "mercado cerrado" in with_open[0] and "2 abierta" in with_open[1]
