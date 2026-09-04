"""Provider de datos HISTORICOS para backtest, via el paquete `duka`
(descarga y decodifica los ficheros tick .bi5 publicos de Dukascopy).

Por que Dukascopy y no reimplementar el parser binario a mano: el formato
.bi5 (LZMA + registro binario de 20 bytes por tick) es delicado de decodificar
correctamente, y un error de bytes silencioso seria exactamente el tipo de
"vela corrupta" que data/validation.py deberia atrapar, pero es mejor no
introducirlo. `duka` ya lo hace y lo usa la comunidad quant de forma habitual.

AVISO PARA CUANDO SE INSTALEN LAS DEPENDENCIAS (recordatorio explicito, ver
tambien el resumen que te doy en el chat): la version exacta de las flags del
CLI de `duka` puede variar entre versiones del paquete. La primera vez que se
instale en el mini PC, correr `duka --help` y confirmar que las flags de
`_DUKA_CMD` de abajo siguen siendo validas antes de fiarse de una descarga en
produccion. No lo he podido verificar en este entorno porque no hay conexion
de red disponible durante el desarrollo.

Convencion de tiempo: se asume que el CSV que produce `duka` para barras
agregadas (H1) usa el timestamp de APERTURA de la vela (igual que MT5). Se
normaliza sumando el timeframe para respetar el contrato de "ts = cierre" de
DataProvider. Esto tambien debe confirmarse contra una descarga real antes de
dar por buena la primera ingesta.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from data.providers.base import DataProvider, DataProviderError, OHLCVRequest, Timeframe

logger = logging.getLogger(__name__)

# Timeframe base que pedimos siempre a duka; H4 y D1 se derivan por resampleo
# en pandas a partir de H1 (resamplear OHLC ya cerradas es una operacion segura
# y determinista: open=primero, high=max, low=min, close=ultimo, volume=suma).
_BASE_TIMEFRAME = "H1"

_RESAMPLE_RULE = {
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
}


class DukascopyProvider(DataProvider):
    name = "dukascopy"

    def __init__(self, cache_dir: str | Path | None = None):
        self._cache_dir = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "duka_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def is_available(self) -> bool:
        try:
            result = subprocess.run(["duka", "--help"], capture_output=True, timeout=10)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=60))
    def fetch_ohlcv(self, request: OHLCVRequest) -> pd.DataFrame:
        h1_df = self._fetch_h1(request.instrument, request.start, request.end)

        if request.timeframe == Timeframe.H1:
            df = h1_df
        elif request.timeframe in _RESAMPLE_RULE:
            df = self._resample(h1_df, _RESAMPLE_RULE[request.timeframe])
        else:
            raise DataProviderError(f"timeframe no soportado: {request.timeframe}")

        now = pd.Timestamp(datetime.now(UTC))
        df = df[df["ts"] < now].sort_values("ts").reset_index(drop=True)
        self.validate_columns(df)
        return df

    def _fetch_h1(self, instrument: str, start: datetime, end: datetime) -> pd.DataFrame:
        out_dir = self._cache_dir / instrument
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "duka",
            instrument,
            "-s", start.strftime("%Y-%m-%d"),
            "-e", end.strftime("%Y-%m-%d"),
            "-t", _BASE_TIMEFRAME,
            "-f", str(out_dir),
        ]
        logger.info("Descargando historico Dukascopy: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            raise DataProviderError(
                f"duka fallo para {instrument} [{start.date()}..{end.date()}]: {result.stderr.strip()}"
            )

        csv_path = self._locate_output_csv(out_dir, instrument)
        if csv_path is None:
            raise DataProviderError(
                f"duka termino sin error pero no se encontro el CSV de salida en {out_dir}"
            )

        raw = pd.read_csv(csv_path)
        raw.columns = [c.strip().lower() for c in raw.columns]
        # Normalizacion defensiva de nombres de columna: distintas versiones de
        # duka han usado 'timestamp'/'time' y 'volume'/'ask_volume'.
        ts_col = next((c for c in ("timestamp", "time", "datetime") if c in raw.columns), None)
        if ts_col is None:
            raise DataProviderError(f"no se reconoce la columna de tiempo en {csv_path.name}: {list(raw.columns)}")

        open_ts = pd.to_datetime(raw[ts_col], utc=True)
        df = pd.DataFrame({
            "ts": open_ts + Timeframe.H1.timedelta,  # apertura -> cierre
            "open": raw["open"],
            "high": raw["high"],
            "low": raw["low"],
            "close": raw["close"],
            "volume": raw.get("volume", raw.get("ask_volume", 0.0)),
        })
        return df

    @staticmethod
    def _locate_output_csv(out_dir: Path, instrument: str) -> Path | None:
        candidates = sorted(out_dir.glob(f"*{instrument}*.csv"))
        return candidates[-1] if candidates else None

    @staticmethod
    def _resample(h1_df: pd.DataFrame, rule: str) -> pd.DataFrame:
        indexed = h1_df.set_index("ts")
        agg = indexed.resample(rule, label="right", closed="right").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        agg = agg.dropna(subset=["open", "high", "low", "close"])
        return agg.reset_index()
