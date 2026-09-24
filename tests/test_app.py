"""Detalles de la capa de Discord que se pueden probar sin arrancar el cliente."""

from __future__ import annotations

import pandas as pd

from finance_bot.discord_bot import app as app_module


def test_presence_says_market_closed(monkeypatch) -> None:
    """Con el mercado cerrado no se muestran precios viejos como si fueran de
    ahora: el estado del bot dice claramente que esta cerrado."""
    monkeypatch.setattr(app_module, "forex_market_open", lambda now: False)
    # el metodo no usa self en la rama de mercado cerrado: basta un stub
    texts = app_module.FinanceBot._presence_texts(object(), None, {})
    assert texts == ["🌙 mercado cerrado"]

    with_open = app_module.FinanceBot._presence_texts(object(), pd.DataFrame({"key": ["k1", "k2"]}), {})
    assert "mercado cerrado" in with_open[0] and "2 abierta" in with_open[1]
