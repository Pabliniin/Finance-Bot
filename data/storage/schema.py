"""Definiciones de tabla SQLAlchemy Core (no ORM completo: para un pipeline de
datos, Core da inserts/upserts tipados sin la sobrecarga de mapear entidades).
Debe reflejar exactamente data/storage/migrations/001_init.sql; si se añade
una columna en el SQL, se añade aqui tambien en el mismo cambio.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
)

metadata = MetaData()

ohlcv = Table(
    "ohlcv",
    metadata,
    Column("instrument", String, primary_key=True),
    Column("timeframe", String, primary_key=True),
    Column("ts", DateTime(timezone=True), primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float),
    Column("source", String, primary_key=True),
    Column("ingested_at", DateTime(timezone=True)),
)

calendar_events = Table(
    "calendar_events",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("country", String),
    Column("currency", String),
    Column("event_name", String, nullable=False),
    Column("impact", String, nullable=False),
    Column("actual", String),
    Column("forecast", String),
    Column("previous", String),
    Column("source", String, nullable=False),
    Column("ingested_at", DateTime(timezone=True)),
)

news_headlines = Table(
    "news_headlines",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("headline", String, nullable=False),
    Column("summary", String),
    Column("source", String, nullable=False),
    Column("url", String, nullable=False, unique=True),
    Column("ingested_at", DateTime(timezone=True)),
)

risk_state = Table(
    "risk_state",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)

ingestion_log = Table(
    "ingestion_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True)),
    Column("dataset", String, nullable=False),
    Column("source", String, nullable=False),
    Column("instrument", String),
    Column("rows_received", Integer, nullable=False),
    Column("rows_accepted", Integer, nullable=False),
    Column("issues", JSON),
    Column("status", String, nullable=False),
)
