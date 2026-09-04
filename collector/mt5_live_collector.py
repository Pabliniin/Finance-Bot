"""Entry point NATIVO (Windows, fuera de Docker) que actualiza la cache con
velas en vivo desde MT5/XM. Disenado para lanzarse via Windows Task Scheduler:

    python -m collector.mt5_live_collector

No corre dentro de docker-compose porque MetaTrader5 necesita el terminal
instalado en la misma maquina Windows. Solo escribe en Postgres a traves de
DATABASE_URL_HOST (el puerto que docker-compose publica en localhost); no
importa nada del resto del sistema salvo data/ y config/.

El calendario y las noticias NO se recogen aqui: no dependen de MT5, así que
viven en scheduler/daily_job.py (dentro de Docker, con su propia salida a
internet), para no acoplar el collector nativo a mas de lo estrictamente
necesario.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta

from config.loader import load_config, load_instruments
from config.settings import get_settings
from data.providers.base import DataProviderError, OHLCVRequest, Timeframe
from data.providers.mt5_provider import MT5Provider
from data.storage.cache import get_latest_ts, log_ingestion, upsert_ohlcv
from data.storage.db import get_engine
from data.validation import Severity, validate_ohlcv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("collector_mt5.log", encoding="utf-8")],
)
logger = logging.getLogger("collector.mt5")

_TIMEFRAMES = [Timeframe.H1, Timeframe.H4, Timeframe.D1]


def run() -> int:
    settings = get_settings()
    if not settings.database_url_host:
        logger.error("DATABASE_URL_HOST no esta configurado en .env; abortando.")
        return 1

    app_config = load_config()
    instruments = load_instruments()
    engine = get_engine(settings.database_url_host)

    provider = MT5Provider(
        login=settings.mt5_login,
        password=settings.mt5_password,
        server=settings.mt5_server,
        terminal_path=settings.mt5_terminal_path or None,
    )

    try:
        provider.connect()
    except DataProviderError as exc:
        logger.error("No se pudo conectar a MT5: %s", exc)
        return 1

    backfill_days = app_config.data.history_backfill_years * 365
    now = datetime.now(UTC)
    ok, failed = 0, 0

    try:
        for instrument in instruments:
            for tf in _TIMEFRAMES:
                try:
                    _sync_one(engine, provider, instrument.symbol, tf, now, backfill_days)
                    ok += 1
                except Exception:  # noqa: BLE001 - un fallo puntual no debe tumbar el resto del universo
                    failed += 1
                    logger.exception("Fallo sincronizando %s %s", instrument.symbol, tf.value)
    finally:
        provider.disconnect()

    logger.info("Collector MT5 terminado: %d combinaciones ok, %d fallidas", ok, failed)
    return 0 if failed == 0 else 2


def _sync_one(engine, provider: MT5Provider, symbol: str, tf: Timeframe, now: datetime, backfill_days: int) -> None:
    latest = get_latest_ts(engine, symbol, tf.value, provider.name)
    start = latest + tf.timedelta if latest else now - timedelta(days=backfill_days)

    if start >= now:
        return  # ya al dia

    request = OHLCVRequest(instrument=symbol, timeframe=tf, start=start, end=now)
    df = provider.fetch_ohlcv(request)

    report = validate_ohlcv(df, tf)
    if report.has_errors:
        log_ingestion(
            engine, dataset="ohlcv", source=provider.name, instrument=symbol,
            rows_received=len(df), rows_accepted=0, status="failed", issues=report.to_dict(),
        )
        logger.error("Validacion fallida para %s %s, lote descartado: %s", symbol, tf.value, report.to_dict())
        return

    accepted = upsert_ohlcv(engine, df, symbol, tf.value, provider.name)
    status = "degraded" if any(i.severity == Severity.WARNING for i in report.issues) else "ok"
    log_ingestion(
        engine, dataset="ohlcv", source=provider.name, instrument=symbol,
        rows_received=len(df), rows_accepted=accepted, status=status, issues=report.to_dict() or None,
    )
    logger.info("%s %s: %d velas nuevas (%s)", symbol, tf.value, accepted, status)


if __name__ == "__main__":
    sys.exit(run())
