"""Registro persistente (SQLite) de cada señal enviada, su seguimiento hasta el
cierre con las MISMAS reglas que el backtest, las preferencias del usuario y
el interruptor de seguridad (kill switch).

El seguimiento es de papel: el bot calcula que habria pasado siguiendo el
plan al pie de la letra. No sabe (ni debe saber) que hiciste tu en tu cuenta.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, select
from sqlalchemy.engine import Engine

from finance_bot.config import AppConfig
from finance_bot.data.bars import TF_MINUTES
from finance_bot.data.market import MarketData
from finance_bot.engine.exits import ExitAdvice
from finance_bot.engine.labeling import label_candidates
from finance_bot.engine.signals import Signal

logger = logging.getLogger(__name__)

DB_NAME = "bot.sqlite3"  # dentro de data.store_dir (config/settings.yaml)
metadata = MetaData()

signals_table = Table(
    "signals",
    metadata,
    Column("key", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("symbol", String, nullable=False),
    Column("tf", String, nullable=False),
    Column("direction", Integer, nullable=False),
    Column("plan", String, nullable=False),
    Column("validated", Boolean, nullable=False),
    Column("signal_time", DateTime(timezone=True), nullable=False),
    Column("entry", Float, nullable=False),
    Column("stop", Float, nullable=False),
    Column("tp1", Float, nullable=False),
    Column("tp2", Float, nullable=False),
    Column("r_price", Float, nullable=False),
    Column("p_tp1", Float, nullable=False),
    Column("p_tp2", Float, nullable=False),
    Column("similar_ev", Float),
    Column("max_hours", Float, nullable=False),
    Column("payload", Text, nullable=False),
    Column("status", String, nullable=False, default="open"),  # open | closed
    Column("tp1_notified", Boolean, nullable=False, default=False),
    Column("exit_reason", String),
    Column("exit_time", DateTime(timezone=True)),
    Column("realized_r", Float),
    Column("hit_tp1", Float),
    Column("hit_tp2", Float),
    Column("peak_r", Float),  # mejor R latente visto en vivo (para el aviso de "devolviendo beneficio")
    Column("source", String),  # "bot" (señal del modelo) o "manual" (operacion tuya)
)

# Cada aviso de gestion, con el R que llevaba la operacion en ese momento: asi se
# puede comparar despues "cerrar en el aviso" contra "seguir el plan hasta el final".
advice_table = Table(
    "exit_advice",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("kind", String, nullable=False),
    Column("urgency", String, nullable=False),
    Column("headline", String, nullable=False),
    Column("r_at_advice", Float),
)

settings_table = Table(
    "settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
)


class Tracker:
    def __init__(self, cfg: AppConfig, db_path: Path | None = None):
        self.cfg = cfg
        db_path = db_path or cfg.data.store_path / DB_NAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(f"sqlite:///{db_path}", future=True)
        metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Bases de datos creadas por versiones anteriores: añade las columnas
        nuevas sin perder el historico de señales ya seguidas."""
        with self.engine.begin() as conn:
            existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(signals)")}
            for column in signals_table.columns:
                if column.name not in existing:
                    kind = "FLOAT" if isinstance(column.type, Float) else "TEXT"
                    conn.exec_driver_sql(f"ALTER TABLE signals ADD COLUMN {column.name} {kind}")
                    logger.info("Base de datos actualizada: nueva columna signals.%s", column.name)

    # --- preferencias del usuario --------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(settings_table.c.value).where(settings_table.c.key == key)).first()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(settings_table.delete().where(settings_table.c.key == key))
            conn.execute(settings_table.insert().values(key=key, value=value))

    def capital_eur(self) -> float:
        return float(self.get_setting("capital_eur") or self.cfg.account.capital_eur)

    def risk_pct(self) -> float:
        return float(self.get_setting("risk_pct") or self.cfg.account.risk_pct)

    def mode(self) -> str:
        return self.get_setting("mode") or self.cfg.signals.mode

    def muted_until(self, symbol: str) -> datetime | None:
        raw = self.get_setting(f"mute_{symbol}")
        return datetime.fromisoformat(raw) if raw else None

    # --- señales ----------------------------------------------------------------

    def is_known(self, key: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(signals_table.c.key).where(signals_table.c.key == key)).first() is not None

    def open_signals(self, source: str | None = None) -> pd.DataFrame:
        """Abiertas. Con source="bot" solo las del modelo (las filas antiguas, sin
        columna, son del bot); con "manual" solo las que sigues a mano."""
        query = select(signals_table).where(signals_table.c.status == "open")
        if source == "bot":
            query = query.where((signals_table.c.source == "bot") | (signals_table.c.source.is_(None)))
        elif source is not None:
            query = query.where(signals_table.c.source == source)
        with self.engine.connect() as conn:
            return pd.read_sql(query, conn)

    def closed_signals(self, since: datetime | None = None, source: str = "bot") -> pd.DataFrame:
        """Por defecto solo las señales del bot: las operaciones que sigues a
        mano no tienen probabilidad del modelo y mezclarlas falsearia /stats y
        el interruptor de seguridad."""
        query = select(signals_table).where(signals_table.c.status == "closed")
        if source == "bot":  # las filas antiguas no tienen columna: son del bot
            query = query.where((signals_table.c.source == "bot") | (signals_table.c.source.is_(None)))
        elif source != "todas":
            query = query.where(signals_table.c.source == source)
        if since is not None:
            query = query.where(signals_table.c.exit_time >= since)
        with self.engine.connect() as conn:
            return pd.read_sql(query.order_by(signals_table.c.exit_time), conn)

    def record(self, s: Signal) -> None:
        payload = json.dumps(_signal_payload(s), default=str)
        with self.engine.begin() as conn:
            conn.execute(
                signals_table.insert().values(
                    key=s.key,
                    created_at=datetime.now(UTC),
                    symbol=s.symbol,
                    tf=s.tf,
                    direction=s.direction,
                    plan=s.plan,
                    validated=s.validated,
                    signal_time=s.signal_time.to_pydatetime(),
                    entry=s.entry,
                    stop=s.stop,
                    tp1=s.tp1,
                    tp2=s.tp2,
                    r_price=s.r_price,
                    p_tp1=s.p_tp1,
                    p_tp2=s.p_tp2,
                    similar_ev=s.similar_ev,
                    max_hours=s.max_hours,
                    payload=payload,
                    status="open",
                    tp1_notified=False,
                    source="bot",
                )
            )

    def follow_manual(
        self, symbol: str, tf: str, direction: int, entry: float, stop: float, plan_name: str = "equilibrado"
    ) -> str:
        """Sigue una operacion TUYA con las mismas reglas del plan: te avisa al
        tocar TP1, al cerrarse y si ve motivo para salir antes. No lleva
        probabilidad del modelo (no es un setup suyo) y queda fuera de /stats."""
        if direction not in (1, -1) or entry <= 0 or stop <= 0 or entry == stop:
            raise ValueError("entrada y stop tienen que ser precios distintos y positivos")
        if (direction > 0 and stop >= entry) or (direction < 0 and stop <= entry):
            raise ValueError("en una compra el stop va por debajo de la entrada, y al reves en una venta")
        plan = self.cfg.plans[plan_name]
        r_price = abs(entry - stop)
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        key = f"manual|{symbol}|{tf}|{now.isoformat()}"
        t1, t2 = plan.targets()
        with self.engine.begin() as conn:
            conn.execute(
                signals_table.insert().values(
                    key=key,
                    created_at=datetime.now(UTC),
                    symbol=symbol,
                    tf=tf,
                    direction=direction,
                    plan=plan_name,
                    validated=False,
                    signal_time=now,
                    entry=entry,
                    stop=stop,
                    tp1=entry + direction * r_price * t1,
                    tp2=entry + direction * r_price * t2,
                    r_price=r_price,
                    p_tp1=0.0,  # no hay modelo detras: en los mensajes se muestra "n/d"
                    p_tp2=0.0,
                    similar_ev=None,
                    max_hours=plan.max_bars(self.cfg.timeframes[tf]) * TF_MINUTES[tf] / 60,
                    payload="{}",
                    status="open",
                    tp1_notified=False,
                    source="manual",
                )
            )
        logger.info("Siguiendo operacion manual %s", key)
        return key

    def stop_following(self, key: str) -> bool:
        """Deja de seguir una operacion manual (cerrada por ti a mano)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                signals_table.update()
                .where(signals_table.c.key == key, signals_table.c.source == "manual")
                .values(status="closed", exit_reason="manual", exit_time=datetime.now(UTC))
            )
        return bool(result.rowcount)

    def update_open(self, md: MarketData) -> list[dict]:
        """Resuelve las señales abiertas con las velas mas recientes. Devuelve
        eventos a notificar: TP1 alcanzado o cierre (stop, TP2, break-even,
        tiempo)."""
        events: list[dict] = []
        opened = self.open_signals()
        for _, sig in opened.iterrows():
            tf = sig["tf"]
            plan = self.cfg.plans[sig["plan"]]
            inst = self.cfg.instrument(sig["symbol"])
            signal_time = (
                pd.Timestamp(sig["signal_time"]).tz_convert("UTC")
                if pd.Timestamp(sig["signal_time"]).tzinfo
                else pd.Timestamp(sig["signal_time"], tz="UTC")
            )
            path = md.base_m1(sig["symbol"], start=(signal_time - pd.Timedelta(minutes=5)).to_pydatetime())
            if path.empty:
                continue
            cand = pd.DataFrame(
                {
                    "close_time": [signal_time],
                    "direction": [int(sig["direction"])],
                    "sl_distance": [float(sig["r_price"])],
                    "entry_override": [float(sig["entry"])],
                },
                index=[signal_time],
            )
            max_bars = plan.max_bars(self.cfg.timeframes[tf])
            labels = label_candidates(cand, path, inst, TF_MINUTES[tf], max_bars, plan.targets(), plan.partial, 1)
            label = labels.iloc[0]
            if pd.isna(label["hit_tp1"]):
                continue
            if label["complete"]:
                with self.engine.begin() as conn:
                    conn.execute(
                        signals_table.update()
                        .where(signals_table.c.key == sig["key"])
                        .values(
                            status="closed",
                            exit_reason=label["exit_reason"],
                            exit_time=label["exit_time"].to_pydatetime(),
                            realized_r=float(label["realized_r"]),
                            hit_tp1=float(label["hit_tp1"]),
                            hit_tp2=float(label["hit_tp2"]),
                            tp1_notified=True,
                        )
                    )
                events.append(
                    {
                        "type": "closed",
                        "key": sig["key"],
                        "symbol": sig["symbol"],
                        "tf": tf,
                        "direction": int(sig["direction"]),
                        "reason": label["exit_reason"],
                        "realized_r": float(label["realized_r"]),
                        "hours": float(label["hours_to_exit"]),
                    }
                )
            elif label["hit_tp1"] == 1 and not sig["tp1_notified"]:
                with self.engine.begin() as conn:
                    conn.execute(
                        signals_table.update().where(signals_table.c.key == sig["key"]).values(tp1_notified=True)
                    )
                events.append(
                    {
                        "type": "tp1",
                        "key": sig["key"],
                        "symbol": sig["symbol"],
                        "tf": tf,
                        "direction": int(sig["direction"]),
                        "hours": float(label["hours_to_tp1"]),
                        "partial": float(plan.partial),
                    }
                )
        return events

    # --- avisos de gestion ---------------------------------------------------------

    def update_peak_r(self, key: str, r: float) -> float:
        """Guarda el mejor R latente visto. Devuelve el maximo actualizado."""
        with self.engine.begin() as conn:
            row = conn.execute(select(signals_table.c.peak_r).where(signals_table.c.key == key)).first()
            peak = max(float(row[0]) if row and row[0] is not None else r, r)
            conn.execute(signals_table.update().where(signals_table.c.key == key).values(peak_r=peak))
        return peak

    def peak_r(self, key: str) -> float | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(signals_table.c.peak_r).where(signals_table.c.key == key)).first()
        return float(row[0]) if row and row[0] is not None else None

    def last_advice_at(self, key: str, kind: str) -> datetime | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(advice_table.c.created_at)
                .where(advice_table.c.key == key, advice_table.c.kind == kind)
                .order_by(advice_table.c.created_at.desc())
            ).first()
        if not row or row[0] is None:
            return None
        stamp = pd.Timestamp(row[0])
        return (stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")).to_pydatetime()

    def record_advice(self, advice: ExitAdvice) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                advice_table.insert().values(
                    key=advice.key,
                    created_at=datetime.now(UTC),
                    kind=advice.kind,
                    urgency=advice.urgency,
                    headline=advice.headline,
                    r_at_advice=advice.r_now,
                )
            )

    def first_advice_r(self, key: str) -> tuple[float | None, str | None]:
        """R y motivo del PRIMER aviso de esa señal (para comparar al cerrarla)."""
        with self.engine.connect() as conn:
            row = conn.execute(
                select(advice_table.c.r_at_advice, advice_table.c.headline)
                .where(advice_table.c.key == key)
                .order_by(advice_table.c.created_at)
            ).first()
        return (None, None) if row is None else (row[0], row[1])

    def advice_scoreboard(self) -> pd.DataFrame:
        """Una fila por señal cerrada que tuvo aviso: R si hubieras cerrado en el
        primer aviso vs R real siguiendo el plan. Es la unica forma honesta de
        saber si estos avisos suman o restan."""
        with self.engine.connect() as conn:
            advice = pd.read_sql(select(advice_table), conn)
            closed = pd.read_sql(
                select(
                    signals_table.c.key,
                    signals_table.c.symbol,
                    signals_table.c.tf,
                    signals_table.c.realized_r,
                    signals_table.c.exit_reason,
                ).where(signals_table.c.status == "closed"),
                conn,
            )
        if advice.empty or closed.empty:
            return pd.DataFrame(columns=["key", "symbol", "tf", "kind", "r_at_advice", "realized_r", "exit_reason"])
        first = advice.sort_values("created_at").groupby("key", as_index=False).first()
        merged = first.merge(closed, on="key", how="inner")
        return merged.dropna(subset=["r_at_advice", "realized_r"])[
            ["key", "symbol", "tf", "kind", "r_at_advice", "realized_r", "exit_reason"]
        ]

    # --- interruptor de seguridad -------------------------------------------------

    def kill_switch_status(self) -> tuple[bool, str | None]:
        raw = self.get_setting("kill_switch")
        if not raw:
            return False, None
        data = json.loads(raw)
        return bool(data.get("active")), data.get("reason")

    def evaluate_kill_switch(self) -> str | None:
        """Suspende la emision si el rendimiento real se degrada frente a lo
        esperado. Nunca se reactiva solo: /reactivar tras revisarlo."""
        active, _ = self.kill_switch_status()
        if active:
            return None
        ks = self.cfg.risk.kill_switch
        # Solo señales validadas: el interruptor compara resultados con lo que el
        # modelo prometio. Un setup marcado "sin ventaja" no promete nada.
        closed = self.closed_signals()
        closed = closed[closed["validated"].astype(bool)] if len(closed) else closed
        if len(closed) < ks.min_signals:
            return None
        r = closed["realized_r"].to_numpy(dtype=float)
        equity = np.cumsum(r)
        drawdown = float((np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:] - equity).max())
        reason = None
        if drawdown >= ks.max_drawdown_r:
            reason = f"drawdown real de {drawdown:.1f}R (limite {ks.max_drawdown_r:.1f}R)"
        else:
            expected_hits = closed["p_tp1"].to_numpy(dtype=float)
            observed = float(closed["hit_tp1"].sum())
            # Poisson-binomial aproximada por normal: ¿aciertos reales muy por debajo de lo predicho?
            mean, var = expected_hits.sum(), (expected_hits * (1 - expected_hits)).sum()
            if var > 0:
                z = (observed - mean) / np.sqrt(var)
                if stats.norm.cdf(z) < ks.calibration_alpha:
                    reason = (
                        f"aciertos de TP1 reales ({observed:.0f}) muy por debajo de lo predicho ({mean:.1f}); "
                        f"p = {stats.norm.cdf(z):.4f}"
                    )
        if reason:
            self.set_setting(
                "kill_switch", json.dumps({"active": True, "reason": reason, "since": datetime.now(UTC).isoformat()})
            )
        return reason

    def reset_kill_switch(self) -> None:
        self.set_setting("kill_switch", json.dumps({"active": False, "reason": None}))


def _signal_payload(s: Signal) -> dict:
    data = asdict(s)
    data["signal_time"] = s.signal_time.isoformat()
    data["news"] = [{"time": e.time.isoformat(), "currency": e.currency, "title": e.title} for e in s.news]
    return data
