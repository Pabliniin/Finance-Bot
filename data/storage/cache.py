"""Lectura/escritura de la cache local (TimescaleDB). Unico lugar del proyecto
que ejecuta SQL directamente: cualquier otro modulo que quiera datos pasa por
aqui, nunca hace queries propias. Eso mantiene la garantia point-in-time
(parametro `as_of`) centralizada en un solo sitio, igual que en
data/providers/base.py para el momento de la ingesta.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from data.storage.schema import calendar_events, ingestion_log, news_headlines, ohlcv, risk_state


def upsert_ohlcv(engine: Engine, df: pd.DataFrame, instrument: str, timeframe: str, source: str) -> int:
    if df.empty:
        return 0

    now = datetime.utcnow()
    records = [
        {
            "instrument": instrument,
            "timeframe": timeframe,
            "ts": row.ts.to_pydatetime(),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume) if pd.notna(row.volume) else None,
            "source": source,
            "ingested_at": now,
        }
        for row in df.itertuples(index=False)
    ]

    stmt = insert(ohlcv).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument", "timeframe", "ts", "source"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "ingested_at": stmt.excluded.ingested_at,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(records)


def read_ohlcv(
    engine: Engine,
    instrument: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    source: str | None = None,
    as_of: datetime | None = None,
) -> pd.DataFrame:
    """as_of: si se pasa, NUNCA se devuelven velas con ts > as_of. Es el
    mecanismo que usa el backtest walk-forward para simular "lo que se sabia
    en ese momento", y el que debe usar cualquier feature en produccion que
    quiera evitar leer un dato futuro por error de reloj/huso horario."""
    query = select(ohlcv).where(
        ohlcv.c.instrument == instrument,
        ohlcv.c.timeframe == timeframe,
        ohlcv.c.ts >= start,
        ohlcv.c.ts <= end,
    )
    if source:
        query = query.where(ohlcv.c.source == source)
    if as_of:
        query = query.where(ohlcv.c.ts <= as_of)
    query = query.order_by(ohlcv.c.ts.asc())

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return df


def get_latest_ts(engine: Engine, instrument: str, timeframe: str, source: str) -> datetime | None:
    query = (
        select(ohlcv.c.ts)
        .where(ohlcv.c.instrument == instrument, ohlcv.c.timeframe == timeframe, ohlcv.c.source == source)
        .order_by(ohlcv.c.ts.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(query).first()
    return row[0] if row else None


def read_calendar_events(
    engine: Engine, start: datetime, end: datetime, as_of: datetime | None = None
) -> pd.DataFrame:
    """as_of aqui es doblemente importante: un evento con ts futuro respecto a
    as_of NUNCA debe traer su columna 'actual' resuelta como si ya se supiera
    (en la practica 'actual' esta NULL en la fuente hasta que se publica, pero
    dejamos la garantia explicita para que quien consuma esto no tenga que
    fiarse solo de eso)."""
    query = select(calendar_events).where(calendar_events.c.ts >= start, calendar_events.c.ts <= end)
    if as_of:
        query = query.where(calendar_events.c.ts <= as_of)
    query = query.order_by(calendar_events.c.ts.asc())
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return df


def read_news_headlines(
    engine: Engine, start: datetime, end: datetime, as_of: datetime | None = None
) -> pd.DataFrame:
    query = select(news_headlines).where(news_headlines.c.ts >= start, news_headlines.c.ts <= end)
    if as_of:
        query = query.where(news_headlines.c.ts <= as_of)
    query = query.order_by(news_headlines.c.ts.asc())
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return df


def upsert_calendar_events(engine: Engine, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    now = datetime.utcnow()
    records = df.to_dict(orient="records")
    for r in records:
        r["ingested_at"] = now

    stmt = insert(calendar_events).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["event_id"],
        set_={
            # Los eventos economicos se revisan tras publicarse: actual/forecast
            # cambian de null a un valor, o se corrigen. Por eso es update, no
            # do_nothing, a diferencia de las noticias (inmutables).
            "actual": stmt.excluded.actual,
            "forecast": stmt.excluded.forecast,
            "previous": stmt.excluded.previous,
            "ingested_at": stmt.excluded.ingested_at,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(records)


def upsert_news_headlines(engine: Engine, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    now = datetime.utcnow()
    records = df.to_dict(orient="records")
    for r in records:
        r["ingested_at"] = now

    stmt = insert(news_headlines).values(records)
    stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
    with engine.begin() as conn:
        result = conn.execute(stmt)
    return result.rowcount


def read_recent_ingestion_log(engine: Engine, since: datetime, dataset: str | None = None) -> pd.DataFrame:
    query = select(ingestion_log).where(ingestion_log.c.ts >= since)
    if dataset:
        query = query.where(ingestion_log.c.dataset == dataset)
    query = query.order_by(ingestion_log.c.ts.desc())
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def get_risk_state(engine: Engine, key: str) -> dict | None:
    query = select(risk_state.c.value).where(risk_state.c.key == key)
    with engine.connect() as conn:
        row = conn.execute(query).first()
    return row[0] if row else None


def set_risk_state(engine: Engine, key: str, value: dict) -> None:
    stmt = insert(risk_state).values(key=key, value=value, updated_at=datetime.utcnow())
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"], set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at}
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def log_ingestion(
    engine: Engine,
    dataset: str,
    source: str,
    rows_received: int,
    rows_accepted: int,
    status: str,
    instrument: str | None = None,
    issues: dict | None = None,
) -> None:
    record = {
        "ts": datetime.utcnow(),
        "dataset": dataset,
        "source": source,
        "instrument": instrument,
        "rows_received": rows_received,
        "rows_accepted": rows_accepted,
        "issues": issues,
        "status": status,
    }
    with engine.begin() as conn:
        conn.execute(insert(ingestion_log).values(**record))
