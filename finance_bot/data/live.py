"""Fuentes de datos en vivo. Ambas entregan velas M1 BID en UTC (misma
convencion que el historico), que se añaden al almacen local; el resto del
sistema construye las temporalidades exactamente igual que en el backtest.

- MT5Feed (principal): tiempo real y precios de TU broker. Requiere el
  terminal MetaTrader 5 abierto en el mismo PC Windows.
- DukascopyFeed (respaldo, sin MT5): solo horas completas, publicadas unos
  minutos despues de cerrar. Retraso de hasta ~1 h: valido para H1/H4/D1;
  M15 se desactiva en este modo porque llegaria tarde.

Trampas cubiertas:
- MT5 devuelve los tiempos en HORA DEL SERVIDOR (no UTC). El desfase se mide
  con el ultimo tick; si el mercado esta cerrado se usa la convencion de los
  brokers alineados al cierre de Nueva York (GMT+2 invierno / GMT+3 verano).
- El nombre del simbolo cambia segun broker/cuenta (XAUUSD, GOLD, XAUUSD., ...).
- La vela M1 en formacion nunca se entrega.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import pandas as pd

from finance_bot.config import AppConfig, Secrets
from finance_bot.data.dukascopy import DukascopyClient, DukascopyError, decode_ticks, ticks_hour_url, ticks_to_m1
from finance_bot.data.store import BAR_COLUMNS, empty_bars

logger = logging.getLogger(__name__)

# Errores de MetaTrader 5 que significan "la tuberia con el terminal esta rota"
# (terminal cerrado, reiniciado o actualizandose). Se reconecta y se reintenta.
IPC_ERRORS = (-10001, -10002, -10003, -10004, -10005)
MT5_CONNECT_TIMEOUT_MS = 20_000

SYMBOL_ALIASES = {
    "XAUUSD": ["XAUUSD", "GOLD", "XAUUSD.", "XAUUSDm", "GOLD.", "XAUUSD#", "GOLDm"],
    "EURUSD": ["EURUSD", "EURUSD.", "EURUSDm", "EURUSD#"],
}
# Si ningun nombre conocido encaja, se busca entre TODOS los simbolos del broker
# uno que empiece por alguna de estas bases: cubre sufijos raros por cuenta/broker
# (XAUUSD_raw, GOLDmicro, EURUSD.pro, ...) sin tener que listarlos todos.
SYMBOL_BASES = {
    "XAUUSD": ("XAUUSD", "GOLD"),
    "EURUSD": ("EURUSD",),
}


class LiveFeedError(RuntimeError):
    pass


class LiveFeed(Protocol):
    name: str
    realtime: bool

    def fetch_m1(self, symbol: str, since: datetime) -> tuple[pd.DataFrame, pd.Timestamp]:
        """(velas M1 cerradas desde `since`, instante hasta el que los datos estan completos)."""
        ...

    def quote(self, symbol: str) -> tuple[float, float] | None:
        """(bid, ask) actuales, o None si la fuente no da cotizacion en vivo."""
        ...


def ny_close_server_offset(now: datetime) -> timedelta:
    """Desfase de un servidor alineado al cierre de NY: UTC-offset de NY + 7h
    (GMT+2 en invierno, GMT+3 con horario de verano de EE. UU.)."""
    ny_offset = pd.Timestamp(now).tz_convert("America/New_York").utcoffset() or timedelta(hours=-5)
    return ny_offset + timedelta(hours=7)


class MT5Feed:
    name = "MT5"
    realtime = True

    def __init__(self, secrets: Secrets, symbols: list[str]):
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise LiveFeedError(
                "paquete MetaTrader5 no instalado (pip install -e .[mt5]); solo funciona en Windows"
            ) from exc
        self.mt5 = mt5
        self.secrets = secrets
        self.symbols = list(symbols)
        self._connect()
        self._offset = ny_close_server_offset(datetime.now(UTC))

    def _connect(self) -> None:
        kwargs: dict = {}
        if self.secrets.mt5_login:
            kwargs = {
                "login": self.secrets.mt5_login,
                "password": self.secrets.mt5_password,
                "server": self.secrets.mt5_server,
            }
        kwargs["timeout"] = MT5_CONNECT_TIMEOUT_MS  # nunca colgar el hilo unico esperando al terminal
        ok = (
            self.mt5.initialize(self.secrets.mt5_terminal_path, **kwargs)
            if self.secrets.mt5_terminal_path
            else self.mt5.initialize(**kwargs)
        )
        if not ok:
            raise LiveFeedError(f"no se pudo conectar con MetaTrader 5: {self.mt5.last_error()}")
        self.broker_symbols = {s: self._resolve_symbol(s) for s in self.symbols}

    def _retry(self, call: Callable[[], Any]) -> Any:
        """El terminal se cierra, se actualiza o rompe la tuberia y todo devuelve
        None con un error de IPC. Se reconecta una vez y se vuelve a intentar:
        antes eso dejaba al bot sin datos hasta el siguiente reinicio."""
        result = call()
        if result is not None:
            return result
        code = self.mt5.last_error()[0]
        if code not in IPC_ERRORS:
            return None
        logger.warning("MT5 perdio la conexion (%s); reconectando", self.mt5.last_error())
        with contextlib.suppress(Exception):
            self.mt5.shutdown()
        self._connect()
        return call()

    def _resolve_symbol(self, symbol: str) -> str:
        for candidate in SYMBOL_ALIASES.get(symbol, [symbol]):
            info = self.mt5.symbol_info(candidate)
            if info is not None:
                self.mt5.symbol_select(candidate, True)
                if candidate != symbol:
                    logger.info("MT5: %s se llama '%s' en este broker", symbol, candidate)
                return candidate
        # Ningun nombre conocido: buscar entre TODOS los simbolos del broker uno que
        # empiece por la base. Asi el bot no falla con un broker que nombra el oro raro.
        found = self._search_broker_symbol(symbol)
        if found is not None:
            self.mt5.symbol_select(found, True)
            logger.info("MT5: %s se llama '%s' en este broker (encontrado por busqueda)", symbol, found)
            return found
        raise LiveFeedError(f"el broker no ofrece {symbol} (probados: {SYMBOL_ALIASES.get(symbol)})")

    def _search_broker_symbol(self, symbol: str) -> str | None:
        bases = SYMBOL_BASES.get(symbol)
        if not bases:
            return None
        try:
            all_symbols = self.mt5.symbols_get() or []
        except Exception:  # noqa: BLE001 - si no se puede listar, no es fatal: seguira el fallback
            return None
        names = [name for s in all_symbols if (name := getattr(s, "name", ""))]
        # Se prueban las bases en orden (XAUUSD antes que GOLD) y, dentro de cada
        # una, el nombre mas corto (el estandar antes que GOLDMICRO y similares).
        for base in bases:
            matches = [name for name in names if name.upper().startswith(base)]
            if matches:
                return min(matches, key=len)
        return None

    def _server_offset(self, broker_symbol: str) -> timedelta:
        """Con mercado abierto, el ultimo tick (hora servidor) esta a unos
        segundos de "ahora + desfase": el desfase es la diferencia redondeada a
        horas. Si el tick es viejo (fin de semana), convencion cierre de NY."""
        tick = self.mt5.symbol_info_tick(broker_symbol)
        if tick is not None:
            measured_hours = (tick.time - time.time()) / 3600
            hours = round(measured_hours)
            if abs(measured_hours - hours) * 3600 < 300 and -12 <= hours <= 14:
                self._offset = timedelta(hours=hours)
                return self._offset
        self._offset = ny_close_server_offset(datetime.now(UTC))
        return self._offset

    def fetch_m1(self, symbol: str, since: datetime) -> tuple[pd.DataFrame, pd.Timestamp]:
        broker_symbol = self.broker_symbols[symbol]
        offset = self._server_offset(broker_symbol)
        now = pd.Timestamp.now(tz="UTC")
        minutes = int((now - pd.Timestamp(since)).total_seconds() // 60) + 5
        count = min(max(minutes, 10), 99_000)
        rates = self._retry(lambda: self.mt5.copy_rates_from_pos(broker_symbol, self.mt5.TIMEFRAME_M1, 0, count))
        if rates is None:
            raise LiveFeedError(f"MT5 no devolvio velas de {broker_symbol}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        if df.empty:
            # Sin velas nuevas: los datos siguen completos hasta donde estaban, no hasta "ahora"
            return empty_bars(), pd.Timestamp(since).floor("min")
        index = pd.to_datetime(df["time"], unit="s", utc=True) - offset  # hora servidor -> UTC
        bars = pd.DataFrame(
            {
                "open": df["open"],
                "high": df["high"],
                "low": df["low"],
                "close": df["close"],
                "volume": df["tick_volume"],
            },
        )
        bars.index = pd.DatetimeIndex(index, name="time").as_unit("ns")
        # Completo hasta la ultima vela cerrada que el terminal entrega, no hasta
        # "ahora": si MT5 esta abierto pero sin conexion con el broker, las velas se
        # quedan atras y eso tiene que verse (y bloquear señales por tardias).
        newest = pd.Timestamp(bars.index.max()) + pd.Timedelta(minutes=1)
        complete_until = min(now.floor("min"), newest)
        bars = bars[(bars.index >= pd.Timestamp(since)) & (bars.index + pd.Timedelta(minutes=1) <= complete_until)]
        return bars[BAR_COLUMNS].astype("float64"), complete_until

    def quote(self, symbol: str) -> tuple[float, float] | None:
        tick = self._retry(lambda: self.mt5.symbol_info_tick(self.broker_symbols[symbol]))
        if tick is None or tick.bid <= 0:
            return None
        return float(tick.bid), float(tick.ask)

    def swaps(self, symbol: str) -> tuple[float, float] | None:
        info = self.mt5.symbol_info(self.broker_symbols[symbol])
        return (float(info.swap_long), float(info.swap_short)) if info is not None else None


class DukascopyFeed:
    name = "Dukascopy (retraso ~1h)"
    realtime = False

    def __init__(self, cfg: AppConfig, client: DukascopyClient | None = None):
        self.cfg = cfg
        # En vivo, esperar minutos a Dukascopy dejaria al bot mudo: pocos reintentos y cortos
        self.client = client or DukascopyClient(min_interval=0.4, retries=3, max_backoff=20.0)

    def fetch_m1(self, symbol: str, since: datetime) -> tuple[pd.DataFrame, pd.Timestamp]:
        divisor = self.cfg.instrument(symbol).dukascopy_divisor
        now = datetime.now(UTC)
        today = now.date()
        start_day = max(pd.Timestamp(since).date(), today - timedelta(days=45))
        frames = []
        complete_until = pd.Timestamp(start_day, tz="UTC")
        day: date = start_day
        while day < today:
            try:
                frames.append(self.client.m1_day(symbol, day, divisor))
                complete_until = pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)
            except DukascopyError as exc:
                logger.warning("Dukascopy %s %s: %s", symbol, day, exc)
                break
            day += timedelta(days=1)
        if day == today:
            # solo las horas que faltan desde `since`: antes se bajaba el dia entero en cada escaneo
            first_hour = pd.Timestamp(since).hour if pd.Timestamp(since).date() == today else 0
            for hour in range(first_hour, now.hour):
                hour_start = pd.Timestamp(datetime(today.year, today.month, today.day, hour, tzinfo=UTC))
                try:
                    raw = self.client.get(ticks_hour_url(symbol, hour_start))
                except DukascopyError as exc:
                    logger.warning("Dukascopy %s %sh: %s", symbol, hour, exc)
                    break
                if raw is None:
                    break  # hora aun no publicada: los datos estan completos hasta aqui
                frames.append(ticks_to_m1(decode_ticks(raw, hour_start, divisor)))
                complete_until = hour_start + pd.Timedelta(hours=1)
        frames = [f for f in frames if not f.empty]
        bars = pd.concat(frames).sort_index() if frames else empty_bars()
        bars = bars[bars.index >= pd.Timestamp(since)]
        return bars, complete_until

    def quote(self, symbol: str) -> tuple[float, float] | None:
        return None


def create_feed(cfg: AppConfig, secrets: Secrets) -> LiveFeed:
    source = cfg.data.live_source
    if source in ("auto", "mt5"):
        try:
            return MT5Feed(secrets, list(cfg.instruments))
        except LiveFeedError as exc:
            if source == "mt5":
                raise
            logger.warning("MT5 no disponible (%s); usando Dukascopy con retraso de ~1h", exc)
    return DukascopyFeed(cfg)
