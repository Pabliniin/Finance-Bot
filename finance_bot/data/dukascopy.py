"""Cliente del datafeed publico de Dukascopy (gratuito, sin clave).

Formatos verificados contra datos reales (ver tests/test_dukascopy.py):
- Velas BID, LZMA, registros de 24 bytes big-endian: segundos desde el inicio
  del fichero, open, CLOSE, LOW, HIGH (enteros escalados, ¡en ese orden!) y
  volumen float. Un fichero por dia para M1 (.../AAAA/MM/DD/BID_candles_min_1.bi5)
  y uno por mes para H1 (.../AAAA/MM/BID_candles_hour_1.bi5). El mes va de 00 a 11.
- Ticks por hora (.../AAAA/MM/DD/HHh_ticks.bi5), registros de 20 bytes:
  milisegundos, ask, bid, volumen ask, volumen bid. Una hora se publica pocos
  minutos despues de cerrar; la hora en curso nunca esta disponible.

Los periodos sin ticks vienen como velas planas con volumen 0: se descartan
(MT5 tampoco crea vela sin ticks).

Uso responsable: el servidor responde 503 si se le hacen muchas peticiones en
paralelo. El cliente trabaja con UNA conexion, una pausa minima entre
peticiones y esperas largas ante un 503. Es un recurso gratuito: no abusar.
"""

from __future__ import annotations

import logging
import lzma
import random
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

from finance_bot.data.store import BAR_COLUMNS, BarStore, empty_bars

logger = logging.getLogger(__name__)

BASE_URL = "https://datafeed.dukascopy.com/datafeed"

CANDLE_DTYPE = np.dtype([("t", ">i4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"), ("v", ">f4")])
TICK_DTYPE = np.dtype([("ms", ">i4"), ("ask", ">i4"), ("bid", ">i4"), ("ask_vol", ">f4"), ("bid_vol", ">f4")])


class DukascopyError(RuntimeError):
    pass


def m1_day_url(symbol: str, day: date) -> str:
    return f"{BASE_URL}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/BID_candles_min_1.bi5"


def h1_month_url(symbol: str, year: int, month: int) -> str:
    return f"{BASE_URL}/{symbol}/{year}/{month - 1:02d}/BID_candles_hour_1.bi5"


def ticks_hour_url(symbol: str, hour: datetime) -> str:
    return f"{BASE_URL}/{symbol}/{hour.year}/{hour.month - 1:02d}/{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"


def decode_candles(raw: bytes | None, period_start: pd.Timestamp, divisor: float) -> pd.DataFrame:
    if not raw:
        return empty_bars()
    records = np.frombuffer(lzma.decompress(raw), dtype=CANDLE_DTYPE)
    records = records[records["v"] > 0]
    if len(records) == 0:
        return empty_bars()
    offsets = pd.to_timedelta(records["t"].astype("int64"), unit="s")
    index = pd.DatetimeIndex(period_start + offsets, name="time").as_unit("ns")
    return pd.DataFrame(
        {
            "open": records["o"] / divisor,
            "high": records["h"] / divisor,
            "low": records["l"] / divisor,
            "close": records["c"] / divisor,
            "volume": records["v"].astype("float64"),
        },
        index=index,
    )


def decode_ticks(raw: bytes | None, hour_start: pd.Timestamp, divisor: float) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=["bid", "ask"], index=pd.DatetimeIndex([], tz="UTC", name="time"))
    records = np.frombuffer(lzma.decompress(raw), dtype=TICK_DTYPE)
    offsets = pd.to_timedelta(records["ms"].astype("int64"), unit="ms")
    index = pd.DatetimeIndex(hour_start + offsets, name="time").as_unit("ns")
    return pd.DataFrame({"bid": records["bid"] / divisor, "ask": records["ask"] / divisor}, index=index)


def ticks_to_m1(ticks: pd.DataFrame) -> pd.DataFrame:
    """Velas M1 BID a partir de ticks (misma convencion que las velas BID de
    Dukascopy). Volumen = numero de ticks."""
    if ticks.empty:
        return empty_bars()
    grouped = ticks["bid"].resample("1min")
    bars = pd.DataFrame(
        {
            "open": grouped.first(),
            "high": grouped.max(),
            "low": grouped.min(),
            "close": grouped.last(),
            "volume": grouped.count().astype("float64"),
        }
    )
    bars = bars[bars["volume"] > 0]
    bars.index.name = "time"
    return bars[BAR_COLUMNS]


class DukascopyClient:
    def __init__(self, timeout: float = 30.0, retries: int = 8, min_interval: float = 0.6, max_backoff: float = 300.0):
        self.timeout = timeout
        self.retries = retries
        self.max_backoff = max_backoff  # el uso en vivo no puede esperar 5 min: bloquearia el hilo unico
        self.min_interval = min_interval
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "finance-bot/2.0 (historical research, 1 connection)"})
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, url: str) -> bytes | None:
        """None si no existe (404: fin de semana, festivo, periodo no publicado).
        Lanza DukascopyError si falla tras los reintentos: el llamador decide;
        nunca se rellena con datos inventados."""
        last_error: object = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                response = self._session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(120.0, 3.0 * 2**attempt) * (0.7 + 0.6 * random.random()))
                continue
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                return response.content
            last_error = f"HTTP {response.status_code}"
            if response.status_code in (429, 503):
                # Limitacion por volumen: esperar de verdad antes de insistir.
                pause = min(self.max_backoff, 20.0 * 2**attempt)
                logger.warning("Dukascopy limita peticiones (%s); esperando %.0fs", response.status_code, pause)
                time.sleep(pause)
            else:
                time.sleep(min(60.0, 2.0 * 2**attempt))
        raise DukascopyError(f"fallo tras {self.retries} intentos: {url} ({last_error})")

    def m1_day(self, symbol: str, day: date, divisor: float) -> pd.DataFrame:
        return decode_candles(self.get(m1_day_url(symbol, day)), pd.Timestamp(day, tz="UTC"), divisor)

    def h1_month(self, symbol: str, year: int, month: int, divisor: float) -> pd.DataFrame:
        start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
        return decode_candles(self.get(h1_month_url(symbol, year, month)), start, divisor)

    def m1_from_ticks_hour(self, symbol: str, hour: datetime, divisor: float) -> pd.DataFrame:
        hour_start = pd.Timestamp(hour).floor("h")
        hour_start = hour_start.tz_localize("UTC") if hour_start.tzinfo is None else hour_start.tz_convert("UTC")
        return ticks_to_m1(decode_ticks(self.get(ticks_hour_url(symbol, hour_start)), hour_start, divisor))


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def download_h1_history(
    client: DukascopyClient,
    store: BarStore,
    symbol: str,
    divisor: float,
    start: date,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Meses COMPLETOS desde `start` que aun no esten descargados (el mes en
    curso se cubre con velas M1 diarias). Reanudable."""
    today = datetime.now(UTC).date()
    last_complete_month_end = today.replace(day=1) - timedelta(days=1)
    done = store.fetched(symbol)
    total = 0
    for year, month in _months_between(start, last_complete_month_end):
        key = f"{year:04d}-{month:02d}"
        if key in done:
            continue
        try:
            bars = client.h1_month(symbol, year, month, divisor)
        except DukascopyError as exc:
            logger.warning("%s %s: %s", symbol, key, exc)
            continue
        total += store.write(symbol, bars)
        store.mark_fetched(symbol, {key})
        if progress and month == 12:
            progress(f"{symbol} H1 {year}: completo")
    return total


def download_m1_history(
    client: DukascopyClient,
    store: BarStore,
    symbol: str,
    divisor: float,
    start: date,
    progress: Callable[[str], None] | None = None,
    prefer_existing: bool = False,
) -> int:
    """Dias COMPLETOS (UTC) desde `start` hasta ayer. Reanudable; escribe en
    bloques mensuales para no perder trabajo si se interrumpe.

    `prefer_existing=True` (consolidacion nocturna): rellena huecos sin pisar las
    velas que MT5 ya escribio en vivo, para no re-etiquetar señales abiertas."""
    today = datetime.now(UTC).date()
    done = store.fetched(symbol)
    days = [start + timedelta(days=i) for i in range((today - start).days)]
    pending = [d for d in days if d.isoformat() not in done]
    total = 0
    batch: list[pd.DataFrame] = []
    batch_days: set[str] = set()

    def flush() -> None:
        nonlocal total
        if batch:
            total += store.write(symbol, pd.concat(batch), prefer_existing=prefer_existing)
        store.mark_fetched(symbol, batch_days)
        batch.clear()
        batch_days.clear()

    for i, day in enumerate(pending):
        try:
            bars = client.m1_day(symbol, day, divisor)
        except DukascopyError as exc:
            logger.warning("%s %s: %s", symbol, day, exc)
            continue
        if not bars.empty:
            batch.append(bars)
        batch_days.add(day.isoformat())
        is_last = i == len(pending) - 1
        if is_last or pending[i + 1].month != day.month:
            flush()
            if progress:
                progress(f"{symbol} M1 {day.year}-{day.month:02d}: completo")
    return total
