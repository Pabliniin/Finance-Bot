"""Tablas SQLAlchemy Core del modulo tracking/, reflejando
data/storage/migrations/003_tracking.sql. Separadas de data/storage/schema.py
a proposito: esas tablas son "datos de mercado", estas son "estado de
nuestras propias decisiones" - dominios distintos aunque compartan el mismo
Postgres.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, MetaData, String, Table

metadata = MetaData()

signals = Table(
    "signals",
    metadata,
    Column("signal_id", String, primary_key=True),
    Column("ts_generated", DateTime(timezone=True), nullable=False),
    Column("strategy", String, nullable=False),
    Column("instrument", String, nullable=False),
    Column("timeframe", String, nullable=False),
    Column("direction", String, nullable=False),
    Column("entry_price", Float, nullable=False),
    Column("stop_loss", Float, nullable=False),
    Column("take_profit", Float, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("reason", String, nullable=False),
    Column("status", String, nullable=False),
    Column("rejection_reason", String),
    Column("created_at", DateTime(timezone=True)),
)

trades = Table(
    "trades",
    metadata,
    Column("trade_id", String, primary_key=True),
    Column("signal_id", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("instrument", String, nullable=False),
    Column("strategy", String, nullable=False),
    Column("direction", String, nullable=False),
    Column("ts_open", DateTime(timezone=True), nullable=False),
    Column("ts_close", DateTime(timezone=True)),
    Column("entry_fill", Float, nullable=False),
    Column("exit_fill", Float),
    Column("lots", Float, nullable=False),
    Column("risk_eur", Float, nullable=False),
    Column("pnl_eur", Float),
    Column("exit_reason", String),
    Column("status", String, nullable=False),
)

equity_curve = Table(
    "equity_curve",
    metadata,
    Column("ts", DateTime(timezone=True), primary_key=True),
    Column("mode", String, primary_key=True),
    Column("equity_eur", Float, nullable=False),
)
