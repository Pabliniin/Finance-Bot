-- Esquema inicial: solo lo que necesita la capa de datos (ohlcv, calendario,
-- noticias). Las tablas de señales/trades/metricas se añaden en migraciones
-- posteriores (002_...) cuando lleguemos a los modulos strategy/risk/tracking,
-- para que el esquema crezca al mismo ritmo que el codigo que lo usa.
--
-- Se monta en docker-entrypoint-initdb.d: se aplica automaticamente SOLO la
-- primera vez que el volumen de Postgres esta vacio. Para bases ya existentes,
-- aplicar a mano con `data/storage/migrate.py`.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS ohlcv (
    instrument    TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL,   -- CIERRE de la vela (ver contrato en data/providers/base.py)
    open          DOUBLE PRECISION NOT NULL,
    high          DOUBLE PRECISION NOT NULL,
    low           DOUBLE PRECISION NOT NULL,
    close         DOUBLE PRECISION NOT NULL,
    volume        DOUBLE PRECISION,
    source        TEXT NOT NULL,          -- 'dukascopy' | 'mt5_xm'
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument, timeframe, ts, source)
);

SELECT create_hypertable('ohlcv', by_range('ts'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS ix_ohlcv_instrument_tf ON ohlcv (instrument, timeframe, ts DESC);

CREATE TABLE IF NOT EXISTS calendar_events (
    event_id      TEXT PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL,
    country       TEXT,
    currency      TEXT,
    event_name    TEXT NOT NULL,
    impact        TEXT NOT NULL,
    actual        TEXT,
    forecast      TEXT,
    previous      TEXT,
    source        TEXT NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_calendar_ts ON calendar_events (ts);
CREATE INDEX IF NOT EXISTS ix_calendar_currency ON calendar_events (currency);

CREATE TABLE IF NOT EXISTS news_headlines (
    id                BIGSERIAL PRIMARY KEY,
    ts                TIMESTAMPTZ NOT NULL,   -- hora de publicacion real (nunca hora de ingesta)
    headline          TEXT NOT NULL,
    summary           TEXT,
    source            TEXT NOT NULL,
    url               TEXT NOT NULL UNIQUE,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_news_ts ON news_headlines (ts);

-- Tabla de auditoria de validacion: cada lote ingerido deja constancia de que
-- se comprobo (o que fallo) antes de aceptarse, para poder responder a
-- "/estado" con la ultima validacion real, no solo "ultima escritura".
CREATE TABLE IF NOT EXISTS ingestion_log (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    dataset         TEXT NOT NULL,    -- 'ohlcv' | 'calendar' | 'news'
    source          TEXT NOT NULL,
    instrument      TEXT,
    rows_received   INTEGER NOT NULL,
    rows_accepted   INTEGER NOT NULL,
    issues          JSONB,
    status          TEXT NOT NULL     -- 'ok' | 'degraded' | 'failed'
);

CREATE INDEX IF NOT EXISTS ix_ingestion_log_ts ON ingestion_log (ts DESC);
