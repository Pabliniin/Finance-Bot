"""Resuelve el tipo de cambio divisa_de_cotizacion -> EUR a partir de la
propia cache de precios (el cruce EURxxx correspondiente), para poder
convertir el P&L de cualquier par a EUR sin depender de una fuente de datos
adicional. Ver la limitacion ya documentada en risk/position_sizing.py sobre
por que esto no puede asumirse 1:1.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.engine import Engine

from config.instrument_specs import quote_currency
from data.storage.cache import read_ohlcv


class CurrencyConversionError(RuntimeError):
    pass


def resolve_quote_to_eur_rate(
    engine: Engine, instrument: str, as_of: datetime, source: str, timeframe: str = "H1"
) -> float:
    quote = quote_currency(instrument)
    if quote == "EUR":
        return 1.0

    eur_cross = f"EUR{quote}"
    window_start = as_of - timedelta(days=3)
    df = read_ohlcv(engine, eur_cross, timeframe, window_start, as_of, source=source, as_of=as_of)
    if df.empty:
        raise CurrencyConversionError(
            f"no hay precio en cache para {eur_cross} (necesario para convertir el P&L de "
            f"{instrument}, cotizado en {quote}, a EUR)"
        )
    latest_price = float(df.iloc[-1]["close"])
    if latest_price <= 0:
        raise CurrencyConversionError(f"precio invalido en cache para {eur_cross}: {latest_price}")
    return 1.0 / latest_price
