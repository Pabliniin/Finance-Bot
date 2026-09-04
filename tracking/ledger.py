"""Registro persistente de señales y operaciones. Toda señal generada por
strategy/ pasa por aqui ANTES de saber si el riesgo la aprueba, para poder
auditar tambien lo que se descarto (requisito de honestidad del proyecto: no
solo se ven las señales "bonitas" que se ejecutaron).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from tracking.schema import equity_curve, signals, trades


def new_signal_id() -> str:
    return uuid.uuid4().hex


def new_trade_id() -> str:
    return uuid.uuid4().hex


def record_signal(
    engine: Engine,
    ts_generated: datetime,
    strategy: str,
    instrument: str,
    timeframe: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    confidence: float,
    reason: str,
    status: str = "pending",
    rejection_reason: str | None = None,
) -> str:
    signal_id = new_signal_id()
    record = {
        "signal_id": signal_id, "ts_generated": ts_generated, "strategy": strategy,
        "instrument": instrument, "timeframe": timeframe, "direction": direction,
        "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
        "confidence": confidence, "reason": reason, "status": status,
        "rejection_reason": rejection_reason, "created_at": datetime.utcnow(),
    }
    with engine.begin() as conn:
        conn.execute(insert(signals).values(**record))
    return signal_id


def update_signal_status(engine: Engine, signal_id: str, status: str, rejection_reason: str | None = None) -> None:
    stmt = signals.update().where(signals.c.signal_id == signal_id).values(
        status=status, rejection_reason=rejection_reason
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def open_trade(
    engine: Engine,
    signal_id: str,
    mode: str,
    instrument: str,
    strategy: str,
    direction: str,
    ts_open: datetime,
    entry_fill: float,
    lots: float,
    risk_eur: float,
) -> str:
    trade_id = new_trade_id()
    record = {
        "trade_id": trade_id, "signal_id": signal_id, "mode": mode, "instrument": instrument,
        "strategy": strategy, "direction": direction, "ts_open": ts_open, "ts_close": None,
        "entry_fill": entry_fill, "exit_fill": None, "lots": lots, "risk_eur": risk_eur,
        "pnl_eur": None, "exit_reason": None, "status": "open",
    }
    with engine.begin() as conn:
        conn.execute(insert(trades).values(**record))
    return trade_id


def close_trade(
    engine: Engine, trade_id: str, ts_close: datetime, exit_fill: float, pnl_eur: float, exit_reason: str
) -> None:
    stmt = trades.update().where(trades.c.trade_id == trade_id).values(
        ts_close=ts_close, exit_fill=exit_fill, pnl_eur=pnl_eur, exit_reason=exit_reason, status="closed"
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def record_equity(engine: Engine, ts: datetime, mode: str, equity_eur: float) -> None:
    stmt = insert(equity_curve).values(ts=ts, mode=mode, equity_eur=equity_eur)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts", "mode"], set_={"equity_eur": stmt.excluded.equity_eur}
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def read_open_trades(engine: Engine, mode: str) -> pd.DataFrame:
    query = select(trades).where(trades.c.mode == mode, trades.c.status == "open").order_by(trades.c.ts_open)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def read_open_trades_with_signal(engine: Engine, mode: str) -> pd.DataFrame:
    """Join trades+signals: las operaciones abiertas no guardan su propio
    stop/target (serian datos duplicados de la señal que las origino), asi
    que quien necesite evaluar si una posicion abierta debe cerrarse
    (tracking/paper_trader.py) los lee de aqui."""
    query = (
        select(
            trades.c.trade_id, trades.c.signal_id, trades.c.instrument, trades.c.strategy,
            trades.c.direction, trades.c.ts_open, trades.c.entry_fill, trades.c.lots, trades.c.risk_eur,
            signals.c.stop_loss, signals.c.take_profit, signals.c.timeframe,
        )
        .select_from(trades.join(signals, trades.c.signal_id == signals.c.signal_id))
        .where(trades.c.mode == mode, trades.c.status == "open")
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def read_trades(engine: Engine, mode: str, start: datetime, end: datetime) -> pd.DataFrame:
    query = select(trades).where(
        trades.c.mode == mode, trades.c.ts_close.is_not(None),
        trades.c.ts_close >= start, trades.c.ts_close <= end,
    ).order_by(trades.c.ts_close)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def read_equity_curve(engine: Engine, mode: str, start: datetime, end: datetime) -> pd.Series:
    query = select(equity_curve.c.ts, equity_curve.c.equity_eur).where(
        equity_curve.c.mode == mode, equity_curve.c.ts >= start, equity_curve.c.ts <= end
    ).order_by(equity_curve.c.ts)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    if df.empty:
        return pd.Series(dtype=float, name="equity_eur")
    return df.set_index("ts")["equity_eur"]


def realized_pnl_eur(engine: Engine, mode: str, start: datetime, end: datetime) -> float:
    """Usado por risk/limits.py para el kill switch de perdida diaria/semanal:
    se calcula al vuelo desde trades cerrados, nunca se guarda un contador
    aparte que pueda desincronizarse."""
    df = read_trades(engine, mode, start, end)
    return float(df["pnl_eur"].sum()) if not df.empty else 0.0
