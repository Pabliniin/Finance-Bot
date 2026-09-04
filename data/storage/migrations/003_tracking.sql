-- Esquema del modulo tracking/: cada señal emitida queda registrada aunque
-- el riesgo la rechace (para poder auditar "cuantas señales se descartaron y
-- por que", no solo las que se ejecutaron), y cada operacion de paper/live
-- trading referencia la señal que la origino.

CREATE TABLE IF NOT EXISTS signals (
    signal_id         TEXT PRIMARY KEY,
    ts_generated      TIMESTAMPTZ NOT NULL,
    strategy          TEXT NOT NULL,
    instrument        TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    direction         TEXT NOT NULL,
    entry_price       DOUBLE PRECISION NOT NULL,
    stop_loss         DOUBLE PRECISION NOT NULL,
    take_profit       DOUBLE PRECISION NOT NULL,
    confidence        DOUBLE PRECISION NOT NULL,
    reason            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | expired
    rejection_reason  TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_signals_ts ON signals (ts_generated DESC);
CREATE INDEX IF NOT EXISTS ix_signals_instrument ON signals (instrument);

CREATE TABLE IF NOT EXISTS trades (
    trade_id       TEXT PRIMARY KEY,
    signal_id      TEXT NOT NULL REFERENCES signals(signal_id),
    mode           TEXT NOT NULL,     -- 'paper' | 'live'
    instrument     TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    direction      TEXT NOT NULL,
    ts_open        TIMESTAMPTZ NOT NULL,
    ts_close       TIMESTAMPTZ,
    entry_fill     DOUBLE PRECISION NOT NULL,
    exit_fill      DOUBLE PRECISION,
    lots           DOUBLE PRECISION NOT NULL,
    risk_eur       DOUBLE PRECISION NOT NULL,
    pnl_eur        DOUBLE PRECISION,
    exit_reason    TEXT,
    status         TEXT NOT NULL DEFAULT 'open'  -- 'open' | 'closed'
);

CREATE INDEX IF NOT EXISTS ix_trades_status ON trades (mode, status);
CREATE INDEX IF NOT EXISTS ix_trades_ts_close ON trades (ts_close DESC);

CREATE TABLE IF NOT EXISTS equity_curve (
    ts          TIMESTAMPTZ NOT NULL,
    mode        TEXT NOT NULL,
    equity_eur  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (ts, mode)
);
