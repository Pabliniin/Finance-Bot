"""Provider de datos EN VIVO contra el terminal MetaTrader5 (cuenta demo XM).

Importante: este modulo solo puede ejecutarse en el mini PC Windows nativo,
nunca dentro del contenedor Docker (Linux) del bot/scheduler. El paquete
`MetaTrader5` no tiene wheels para Linux y requiere el terminal MT5 corriendo
en la misma maquina. Por eso el import esta protegido: si el paquete no esta
disponible, la clase sigue siendo importable (para que el resto del codigo
Linux pueda referenciar el tipo sin romperse), pero cualquier uso real lanza
DataProviderError con un mensaje claro.

Entry point real: collector/mt5_live_collector.py, ejecutado via Windows Task
Scheduler, que usa esta clase y escribe en Postgres a traves de data/storage.

Nota sobre bias broker-a-broker: estos precios son los de XM, no los de
Dukascopy (usado para backtest). El backtest es una aproximacion; el paper
trading en vivo contra estos mismos datos es la validacion real.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from data.providers.base import DataProvider, DataProviderError, OHLCVRequest, Timeframe

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:  # esperado en Linux/Docker
    mt5 = None
    MT5_AVAILABLE = False

_TIMEFRAME_MAP = {}
if MT5_AVAILABLE:
    _TIMEFRAME_MAP = {
        Timeframe.H1: mt5.TIMEFRAME_H1,
        Timeframe.H4: mt5.TIMEFRAME_H4,
        Timeframe.D1: mt5.TIMEFRAME_D1,
    }


class MT5Provider(DataProvider):
    name = "mt5_xm"

    def __init__(self, login: int, password: str, server: str, terminal_path: str | None = None):
        if not MT5_AVAILABLE:
            raise DataProviderError(
                "El paquete MetaTrader5 no esta instalado o este OS no lo soporta. "
                "MT5Provider solo funciona en el collector nativo de Windows."
            )
        self._login = login
        self._password = password
        self._server = server
        self._terminal_path = terminal_path
        self._connected = False

    def connect(self) -> None:
        kwargs = {"login": self._login, "password": self._password, "server": self._server}
        ok = mt5.initialize(path=self._terminal_path, **kwargs) if self._terminal_path else mt5.initialize(**kwargs)
        if not ok:
            code, desc = mt5.last_error()
            raise DataProviderError(f"fallo al conectar con MT5 ({code}): {desc}")
        self._connected = True
        logger.info("Conectado a MT5 server=%s login=%s", self._server, self._login)

    def disconnect(self) -> None:
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
            self._connected = False

    def is_available(self) -> bool:
        if not MT5_AVAILABLE:
            return False
        try:
            return mt5.terminal_info() is not None
        except Exception:
            return False

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type(DataProviderError),
    )
    def fetch_ohlcv(self, request: OHLCVRequest) -> pd.DataFrame:
        if not self._connected:
            self.connect()

        mt5_tf = _TIMEFRAME_MAP[request.timeframe]
        rates = mt5.copy_rates_range(request.instrument, mt5_tf, request.start, request.end)
        if rates is None:
            code, desc = mt5.last_error()
            raise DataProviderError(
                f"copy_rates_range devolvio None para {request.instrument} {request.timeframe.value} "
                f"({code}): {desc}"
            )
        if len(rates) == 0:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rates)

        # MT5 entrega 'time' = apertura de la vela (UTC). Normalizamos al contrato
        # del proyecto: ts = CIERRE de la vela.
        open_ts = pd.to_datetime(df["time"], unit="s", utc=True)
        df["ts"] = open_ts + request.timeframe.timedelta
        df = df.rename(columns={"tick_volume": "volume"})
        df = df[["ts", "open", "high", "low", "close", "volume"]]

        # Descarta la vela en formacion: su cierre teorico es >= ahora.
        now = pd.Timestamp(datetime.now(UTC))
        df = df[df["ts"] < now]

        df = df.sort_values("ts").reset_index(drop=True)
        self.validate_columns(df)
        return df

    def symbol_info(self, instrument: str) -> dict:
        """Specs reales del broker (digitos, lote minimo, contract size, spread
        actual). Usado para overridear config/instruments.yaml con valores
        verdaderos de XM en vez del fallback estatico."""
        if not self._connected:
            self.connect()
        info = mt5.symbol_info(instrument)
        if info is None:
            raise DataProviderError(f"symbol_info no encontro {instrument} en XM")
        return info._asdict()
