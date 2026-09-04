"""Punto unico de ensamblado: OHLCV + tecnicos + volatilidad + noticias +
calendario -> una matriz de features por barra, con la garantia point-in-time
aplicada de forma centralizada (as_of se propaga a toda lectura de datos).

Cualquier estrategia (strategy/) recibe el resultado de build_feature_matrix,
nunca toca data/storage directamente. Esto es lo que hace imposible que una
estrategia "se salte" el corte point-in-time por error.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.engine import Engine

from data.providers.base import Timeframe
from data.storage.cache import read_calendar_events, read_news_headlines, read_ohlcv
from features.news_features import bulk_event_proximity_features, bulk_news_features
from features.technical import add_technical_indicators
from features.volatility import add_volatility_features

_CALENDAR_LOOKAHEAD = pd.Timedelta(days=30)
_CALENDAR_LOOKBACK = pd.Timedelta(days=30)


def build_feature_matrix(
    engine: Engine,
    instrument: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    source: str,
    as_of: datetime | None = None,
    news_window_hours: int = 24,
) -> pd.DataFrame:
    bars = read_ohlcv(engine, instrument, timeframe.value, start, end, source=source, as_of=as_of)
    if bars.empty:
        return bars

    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars = bars.sort_values("ts").reset_index(drop=True)

    bars = add_technical_indicators(bars)
    bars = add_volatility_features(bars)

    news_start = start - pd.Timedelta(hours=news_window_hours)
    headlines = read_news_headlines(engine, news_start, end, as_of=as_of)

    # El calendario SI puede incluir eventos posteriores a as_of: su fecha es
    # publica de antemano (ver nota en features/news_features.py). Lo que
    # nunca esta disponible antes de tiempo es el campo 'actual', y esa
    # columna ni siquiera se usa en estas features.
    calendar = read_calendar_events(engine, start - _CALENDAR_LOOKBACK, end + _CALENDAR_LOOKAHEAD)

    news_feats = bulk_news_features(bars["ts"], headlines, instrument, news_window_hours)
    event_feats = bulk_event_proximity_features(bars["ts"], calendar, instrument)

    result = pd.concat([bars, news_feats.reset_index(drop=True), event_feats.reset_index(drop=True)], axis=1)
    # instrument/timeframe fijos como columnas: asi cada fila es autocontenida
    # y strategy/*.py no necesita mas argumentos que este DataFrame.
    result["instrument"] = instrument
    result["timeframe"] = timeframe.value
    return result
