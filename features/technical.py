"""Indicadores tecnicos via la libreria `ta` (no calculo manual, requisito del
proyecto). Todos son funciones de ventana movil hacia atras (rolling/trailing)
por construccion de la propia libreria: no miran al futuro. La garantia
point-in-time real, sin embargo, depende de que el DataFrame de entrada ya
este truncado a `ts <= as_of` por quien lo llama (features/pipeline.py) -
estas funciones no vuelven a comprobarlo.
"""

from __future__ import annotations

import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, ADXIndicator, EMAIndicator
from ta.volatility import BollingerBands


def add_technical_indicators(
    df: pd.DataFrame,
    ema_fast: int = 12,
    ema_slow: int = 26,
    rsi_window: int = 14,
    adx_window: int = 14,
    bb_window: int = 20,
    stoch_window: int = 14,
) -> pd.DataFrame:
    """df debe venir ordenado por ts ascendente con columnas open/high/low/close.
    Devuelve una copia con columnas añadidas; no muta el original."""
    out = df.copy()

    out["ema_fast"] = EMAIndicator(out["close"], window=ema_fast).ema_indicator()
    out["ema_slow"] = EMAIndicator(out["close"], window=ema_slow).ema_indicator()

    rsi = RSIIndicator(out["close"], window=rsi_window)
    out["rsi"] = rsi.rsi()

    macd = MACD(out["close"], window_fast=ema_fast, window_slow=ema_slow)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_diff"] = macd.macd_diff()

    adx = ADXIndicator(out["high"], out["low"], out["close"], window=adx_window)
    out["adx"] = adx.adx()
    out["adx_pos"] = adx.adx_pos()
    out["adx_neg"] = adx.adx_neg()

    bb = BollingerBands(out["close"], window=bb_window)
    out["bb_upper"] = bb.bollinger_hband()
    out["bb_lower"] = bb.bollinger_lband()
    out["bb_mid"] = bb.bollinger_mavg()
    out["bb_pct"] = bb.bollinger_pband()

    stoch = StochasticOscillator(out["high"], out["low"], out["close"], window=stoch_window)
    out["stoch_k"] = stoch.stoch()
    out["stoch_d"] = stoch.stoch_signal()

    return out
